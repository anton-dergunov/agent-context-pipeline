"""Read the text out of downloaded Instagram media: frame OCR and speech.

Separate from the CLI because the capture pipeline runs the same two passes and
must not have to synthesize an argument namespace to do it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from info_triage.extractors.artifacts import RAW_DIR
from info_triage.extractors.media.ocr import OCREngine, ocr_images, ocr_video
from info_triage.extractors.media.transcription import (
    Transcriber,
    TranscriptResult,
    write_transcript_outputs,
)

from .prepare import prepare_content

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}


@dataclass(frozen=True, slots=True)
class OCRSettings:
    """Frame sampling and downscaling, at their measured accuracy knees."""

    video_mode: str = "sample"
    video_sample_fps: float = 3.0
    video_max_height: int = 800
    batch_size: int = 8


@dataclass(frozen=True, slots=True)
class TranscriptionSettings:
    backend: str
    model_name: str
    language: str | None = None
    vad: bool = True


def _media_sources(post_dir: Path) -> list[Path]:
    media_dir = post_dir / RAW_DIR / "media"
    return sorted(
        path for path in media_dir.glob("*") if path.is_file() and not path.name.endswith(".part")
    )


def ocr_post(
    post_dir: Path,
    *,
    image_engine: OCREngine | None,
    video_engine: OCREngine | None,
    settings: OCRSettings,
) -> None:
    """OCR every downloaded still and video, then rebuild content.md."""
    ocr_dir = post_dir / RAW_DIR / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, str]] = []
    processed: list[dict[str, str]] = []
    sources = _media_sources(post_dir)

    image_sources = [source for source in sources if source.suffix.lower() in IMAGE_SUFFIXES]
    if image_sources and image_engine is not None:
        try:
            outputs = [ocr_dir / f"{source.stem}.ocr.json" for source in image_sources]
            ocr_images(image_sources, outputs, image_engine, batch_size=settings.batch_size)
            processed.extend(
                {"file": source.name, "engine": image_engine.name} for source in image_sources
            )
        except Exception as exc:
            for source in image_sources:
                errors.append({"file": source.name, "error": f"{type(exc).__name__}: {exc}"})

    for source in sources:
        if source.suffix.lower() not in VIDEO_SUFFIXES or video_engine is None:
            continue
        try:
            ocr_video(
                source,
                ocr_dir / f"{source.stem}.ocr.json",
                video_engine,
                mode=settings.video_mode,
                sample_fps=settings.video_sample_fps,
                max_height=settings.video_max_height,
                batch_size=settings.batch_size,
            )
            processed.append({"file": source.name, "engine": video_engine.name})
        except Exception as exc:
            errors.append({"file": source.name, "error": f"{type(exc).__name__}: {exc}"})

    write_ocr_text(post_dir)
    (ocr_dir / "status.json").write_text(
        json.dumps({"processed": processed, "errors": errors}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prepare_content(post_dir)


def write_ocr_text(post_dir: Path) -> None:
    """Combine the per-media OCR text files into the post's on-screen text."""
    text_files = sorted((post_dir / RAW_DIR / "ocr").glob("*.ocr.txt"))
    texts = [path.read_text(encoding="utf-8").strip() for path in text_files]
    combined = "\n\n".join(text for text in texts if text)
    (post_dir / RAW_DIR / "ocr_text.txt").write_text(
        combined + ("\n" if combined else ""), encoding="utf-8"
    )


def transcribe_post(
    post_dir: Path,
    *,
    transcriber: Transcriber | None,
    settings: TranscriptionSettings,
) -> None:
    """Transcribe the speech in every downloaded video, then rebuild content.md."""
    sources = [
        source for source in _media_sources(post_dir) if source.suffix.lower() in VIDEO_SUFFIXES
    ]
    results: list[TranscriptResult] = []
    for source in sources:
        if transcriber is None:
            results.append(TranscriptResult(source_file=str(source), status="no_audio"))
        else:
            results.append(
                transcriber.transcribe(
                    source,
                    language=settings.language,
                    vad=settings.vad,
                    keep_segments=False,
                )
            )
    write_transcript_outputs(
        post_dir / RAW_DIR,
        results,
        backend=settings.backend,
        model_name=settings.model_name,
    )
    prepare_content(post_dir)
