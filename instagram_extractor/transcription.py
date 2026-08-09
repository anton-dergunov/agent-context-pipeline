"""Shared local speech transcription primitives.

The production extractor does not call this module yet.  It is intentionally
kept independent while the multilingual model benchmark is reviewed.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


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


def has_audio_stream(path: Path) -> bool:
    """Return whether a media container has at least one audio stream."""
    import av

    with av.open(str(path), mode="r") as container:
        return bool(container.streams.audio)


class FasterWhisperTranscriber:
    """One CPU-only faster-whisper model reused over multiple media files."""

    def __init__(self, model_path: Path, threads: int = 1) -> None:
        if threads < 1:
            raise ValueError("threads must be positive")
        from faster_whisper import WhisperModel

        self.model_path = model_path
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
        """Transcribe one file, preserving the spoken language.

        Segment timing is retained for benchmark diagnostics only.  The later
        production writer will deliberately discard it.
        """
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
            return TranscriptResult(
                source_file=source,
                status=status,
                text=text,
                language=getattr(info, "language", None),
                language_probability=float(getattr(info, "language_probability", 0.0)),
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

