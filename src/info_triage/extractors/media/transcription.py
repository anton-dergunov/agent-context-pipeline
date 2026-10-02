"""Shared local multilingual speech transcription and artifact writers."""

from __future__ import annotations

import importlib
import json
import os
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

TRANSCRIPTION_THREAD_ENV = "INFO_TRIAGE_TRANSCRIPTION_THREADS"
TRANSCRIPTION_BACKEND_ENV = "INFO_TRIAGE_TRANSCRIPTION_BACKEND"
TRANSCRIPTION_MODEL_ENV = "INFO_TRIAGE_TRANSCRIPTION_MODEL"

BACKEND_CHOICES = ("best", "faster-whisper", "mlx")
MODEL_CHOICES = ("tiny", "base", "small", "medium", "large-v3", "turbo")

MLX_MODEL_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-turbo",
}

FASTER_WHISPER_MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}

__all__ = [
    "BACKEND_CHOICES",
    "MODEL_CHOICES",
    "FasterWhisperTranscriber",
    "MLXWhisperTranscriber",
    "TranscriptResult",
    "TranscriptSegment",
    "Transcriber",
    "download_faster_whisper_model",
    "has_audio_stream",
    "make_transcriber",
    "production_defaults",
    "resolve_backend_and_model",
    "resolve_transcription_threads",
    "write_transcript_outputs",
]


@dataclass(slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(slots=True)
class TranscriptResult:
    source_file: str
    status: str
    text: str = ""
    language: str | None = None
    language_probability: float | None = None
    duration_seconds: float | None = None
    duration_after_vad_seconds: float | None = None
    inference_seconds: float = 0.0
    segments: list[TranscriptSegment] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Transcriber(Protocol):
    backend: str
    model_name: str
    load_seconds: float

    def transcribe(
        self,
        path: Path,
        *,
        language: str | None = None,
        vad: bool = True,
        beam_size: int = 5,
        keep_segments: bool = True,
    ) -> TranscriptResult: ...


def has_audio_stream(path: Path) -> bool:
    """Return whether a media container has at least one audio stream."""
    import av

    with av.open(str(path), mode="r") as container:
        return bool(container.streams.audio)


def production_defaults(
    system: str | None = None,
    machine: str | None = None,
) -> tuple[str, str]:
    """Return the reviewed backend/model pair for the current platform."""
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "mlx", "medium"
    return "faster-whisper", "small"


def resolve_backend_and_model(
    backend: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Apply CLI, environment, then platform defaults in that order."""
    default_backend, default_model = production_defaults()
    selected_backend = backend or os.environ.get(TRANSCRIPTION_BACKEND_ENV) or "best"
    if selected_backend == "best":
        selected_backend = default_backend
    if selected_backend not in BACKEND_CHOICES[1:]:
        raise ValueError(f"unsupported transcription backend: {selected_backend}")

    selected_model = model or os.environ.get(TRANSCRIPTION_MODEL_ENV) or default_model
    if selected_model not in MODEL_CHOICES:
        raise ValueError(f"unsupported transcription model: {selected_model}")
    if selected_backend == "mlx" and not (
        platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}
    ):
        raise ValueError("the mlx backend requires an Apple Silicon Mac")
    return selected_backend, selected_model


def resolve_transcription_threads(requested: int | None = None) -> int:
    """Resolve the transcription thread count; production defaults to one."""
    if requested is not None:
        if requested < 1:
            raise ValueError("transcription threads must be positive")
        return requested
    configured = os.environ.get(TRANSCRIPTION_THREAD_ENV)
    if configured:
        try:
            value = int(configured)
            if value > 0:
                return value
        except ValueError:
            pass
    return 1


def _pin_native_threads(threads: int) -> None:
    """Pin native CPU helpers before importing either inference runtime."""
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ[name] = str(threads)


def download_faster_whisper_model(model_name: str, cache_dir: Path) -> Path:
    """Download a supported CTranslate2 Whisper model into its runtime directory."""
    model_path = cache_dir / model_name
    if (model_path / "model.bin").exists() and (model_path / "config.json").exists():
        return model_path

    try:
        repo_id = FASTER_WHISPER_MODEL_REPOS[model_name]
    except KeyError as exc:
        raise ValueError(f"unsupported transcription model: {model_name}") from exc

    from huggingface_hub import snapshot_download

    model_path.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=model_path,
        local_files_only=os.environ.get("HF_HUB_OFFLINE") == "1",
        allow_patterns=(
            "config.json",
            "preprocessor_config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.*",
        ),
    )
    return model_path


class FasterWhisperTranscriber:
    """One CPU/int8 faster-whisper model reused over multiple media files."""

    backend = "faster-whisper"

    def __init__(
        self,
        model_path: Path,
        threads: int = 1,
        *,
        model_name: str | None = None,
    ) -> None:
        if threads < 1:
            raise ValueError("threads must be positive")
        _pin_native_threads(threads)
        from faster_whisper import WhisperModel

        self.model_path = model_path
        self.model_name = model_name or model_path.name
        self.threads = threads
        started = time.perf_counter()
        self.model = WhisperModel(
            str(model_path),
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
            num_workers=1,
            local_files_only=True,
        )
        self.load_seconds = time.perf_counter() - started

    def transcribe(
        self,
        path: Path,
        *,
        language: str | None = None,
        vad: bool = True,
        beam_size: int = 5,
        keep_segments: bool = True,
    ) -> TranscriptResult:
        """Transcribe one file without translating its spoken language."""
        source = str(path)
        try:
            if not has_audio_stream(path):
                return TranscriptResult(source_file=source, status="no_audio")
        except Exception as exc:
            return TranscriptResult(
                source_file=source,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )

        started = time.perf_counter()
        try:
            generated, info = self.model.transcribe(
                str(path),
                task="transcribe",
                language=language,
                beam_size=beam_size,
                vad_filter=vad,
            )
            segments = [
                TranscriptSegment(float(item.start), float(item.end), item.text.strip())
                for item in generated
                if item.text.strip()
            ]
            text = " ".join(item.text for item in segments).strip()
            status = "complete" if text else "no_speech"
            probability = getattr(info, "language_probability", None)
            return TranscriptResult(
                source_file=source,
                status=status,
                text=text,
                language=getattr(info, "language", None),
                language_probability=float(probability) if probability is not None else None,
                duration_seconds=float(getattr(info, "duration", 0.0)),
                duration_after_vad_seconds=float(getattr(info, "duration_after_vad", 0.0)),
                inference_seconds=time.perf_counter() - started,
                segments=segments if keep_segments else None,
            )
        except Exception as exc:
            return TranscriptResult(
                source_file=source,
                status="failed",
                inference_seconds=time.perf_counter() - started,
                error=f"{type(exc).__name__}: {exc}",
            )


class MLXWhisperTranscriber:
    """Apple-Silicon Whisper inference using MLX on the Metal GPU."""

    backend = "mlx"

    def __init__(self, model_path: Path, threads: int = 1, *, model_name: str = "medium") -> None:
        if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
            raise RuntimeError("MLX Whisper requires an Apple Silicon Mac")
        if threads < 1:
            raise ValueError("threads must be positive")
        _pin_native_threads(threads)
        try:
            import mlx.core as mx
            import mlx_whisper
        except ImportError as exc:
            raise RuntimeError(
                "MLX Whisper is not installed; run `uv sync --extra mac-transcription`"
            ) from exc

        # Make the compute target explicit. MLX defaults to GPU on macOS, but an
        # explicit device prevents a user-level default from silently changing it.
        mx.set_default_device(mx.gpu)
        self._mx = mx
        self._mlx_whisper = mlx_whisper
        self.model_path = model_path
        self.model_name = model_name
        self.threads = threads
        self.load_seconds = 0.0  # MLX lazily loads on the first transcription.

    def transcribe(
        self,
        path: Path,
        *,
        language: str | None = None,
        vad: bool = True,
        beam_size: int = 5,
        keep_segments: bool = True,
    ) -> TranscriptResult:
        source = str(path)
        try:
            if not has_audio_stream(path):
                return TranscriptResult(source_file=source, status="no_audio")
        except Exception as exc:
            return TranscriptResult(
                source_file=source, status="failed", error=f"{type(exc).__name__}: {exc}"
            )

        started = time.perf_counter()
        try:
            from faster_whisper.audio import decode_audio

            audio = decode_audio(str(path))
            duration = len(audio) / 16_000
            duration_after_vad = duration
            if vad:
                from faster_whisper.vad import VadOptions, get_speech_timestamps

                speech = get_speech_timestamps(audio, VadOptions())
                if not speech:
                    return TranscriptResult(
                        source_file=source,
                        status="no_speech",
                        duration_seconds=duration,
                        duration_after_vad_seconds=0.0,
                        inference_seconds=time.perf_counter() - started,
                    )
                import numpy as np

                audio = np.concatenate([audio[item["start"] : item["end"]] for item in speech])
                duration_after_vad = len(audio) / 16_000

            output = self._mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=str(self.model_path),
                task="transcribe",
                language=language,
                # MLX Whisper 0.4 does not implement beam search. Temperature
                # zero gives deterministic greedy decoding on this backend.
                temperature=0.0,
                fp16=True,
                verbose=None,
                word_timestamps=False,
            )
            text = str(output.get("text", "")).strip()
            probability = None
            if language is None and output.get("language"):
                # mlx-whisper returns only the winning language. Reuse the
                # already-loaded model for the corresponding probability so no
                # second model copy is created in unified memory.
                try:
                    module = importlib.import_module("mlx_whisper.transcribe")
                    model = module.ModelHolder.model
                    mel = module.log_mel_spectrogram(audio, n_mels=model.dims.n_mels)
                    mel = module.pad_or_trim(mel, module.N_FRAMES, axis=-2).astype(self._mx.float16)
                    _, probabilities = model.detect_language(mel)
                    probability = float(probabilities[output["language"]])
                except Exception:
                    probability = None
            segments = [
                TranscriptSegment(
                    float(item["start"]), float(item["end"]), str(item["text"]).strip()
                )
                for item in output.get("segments", [])
                if str(item.get("text", "")).strip()
            ]
            return TranscriptResult(
                source_file=source,
                status="complete" if text else "no_speech",
                text=text,
                language=output.get("language"),
                language_probability=probability,
                duration_seconds=duration,
                duration_after_vad_seconds=duration_after_vad,
                inference_seconds=time.perf_counter() - started,
                segments=segments if keep_segments else None,
            )
        except Exception as exc:
            return TranscriptResult(
                source_file=source,
                status="failed",
                inference_seconds=time.perf_counter() - started,
                error=f"{type(exc).__name__}: {exc}",
            )

    def close(self) -> None:
        self._mx.clear_cache()


def _download_mlx_model(model_name: str, cache_dir: Path) -> Path:
    model_path = cache_dir / "mlx" / model_name
    if (model_path / "config.json").exists() and (model_path / "weights.npz").exists():
        return model_path
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("huggingface-hub is required to download an MLX model") from exc
    model_path.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MLX_MODEL_REPOS[model_name],
        local_dir=model_path,
        local_files_only=os.environ.get("HF_HUB_OFFLINE") == "1",
    )
    return model_path


def make_transcriber(
    backend: str,
    model_name: str,
    cache_dir: Path,
    threads: int = 1,
) -> Transcriber:
    """Download/cache the selected model and build the requested backend."""
    if backend == "faster-whisper":
        path = download_faster_whisper_model(model_name, cache_dir)
        return FasterWhisperTranscriber(path, threads, model_name=model_name)
    if backend == "mlx":
        path = _download_mlx_model(model_name, cache_dir)
        return MLXWhisperTranscriber(path, threads, model_name=model_name)
    raise ValueError(f"unsupported transcription backend: {backend}")


def write_transcript_outputs(
    post_dir: Path,
    results: list[TranscriptResult],
    *,
    backend: str,
    model_name: str,
) -> None:
    """Write timestamp-free per-media and combined production artifacts."""
    output_dir = post_dir / "transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    processed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    text_results: list[tuple[str, str]] = []

    for result in results:
        source_name = Path(result.source_file).name
        stem = Path(source_name).stem
        text = result.text.strip()
        (output_dir / f"{stem}.txt").write_text(text + ("\n" if text else ""), encoding="utf-8")
        payload = {
            "source_file": source_name,
            "status": result.status,
            "text": text,
            "backend": backend,
            "model": model_name,
            "language": result.language,
            "language_probability": result.language_probability,
            "error": result.error,
        }
        (output_dir / f"{stem}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        processed.append(
            {"file": source_name, "status": result.status, "language": result.language}
        )
        if result.error:
            errors.append({"file": source_name, "error": result.error})
        if text:
            text_results.append((source_name, text))

    if len(text_results) == 1:
        combined = text_results[0][1]
    else:
        combined = "\n\n".join(f"[{name}]\n{text}" for name, text in text_results)
    (post_dir / "transcript.txt").write_text(
        combined + ("\n" if combined else ""), encoding="utf-8"
    )
    successful = any(item["status"] != "failed" for item in processed)
    if errors:
        outcome = "partial" if successful else "failed"
    else:
        outcome = "complete"
    (output_dir / "status.json").write_text(
        json.dumps(
            {
                "outcome": outcome,
                "backend": backend,
                "model": model_name,
                "processed": processed,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
