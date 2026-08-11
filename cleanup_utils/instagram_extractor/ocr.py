from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
from PIL import Image
from rapidfuzz.fuzz import ratio

from .dedup import _key, lines_to_frame, merge_frames
from .engines import OCREngine, make_engine  # re-exported for callers
from .models import OCRFrame, OCRLine

__all__ = [
    "OCREngine",
    "make_engine",
    "ocr_image",
    "ocr_images",
    "ocr_video",
    "rededuplicate_ocr_result",
    "filter_thresholds",
]

#: Text must be visible this long to count as an overlay rather than a flicker.
MIN_VISIBLE_SECONDS = 0.10
#: Very short strings ("3.", "$0") need longer exposure before they are trusted.
SHORT_TEXT_MIN_VISIBLE_SECONDS = 0.25
#: Alphanumeric length at or below which a string counts as "short".
SHORT_TEXT_MAX_CHARACTERS = 3


def filter_thresholds(effective_fps: float) -> tuple[int, int]:
    """Observation counts for (normal text, short text) at a given OCR rate.

    ``observations`` counts frames that were actually OCR'd, so a fixed count
    silently tightens as sampling gets sparser. Expressing the thresholds as a
    duration keeps them meaningful at any sampling rate.

    Rounding up reads as "visible for at least this long" and reproduces the
    original hardcoded behaviour on real footage: (3, 8) at 30 fps, (3, 6) at
    24 fps and (3, 7) at 25 fps, where a plain round() would have loosened the
    24/25 fps cases to 2 and admitted single-frame noise.
    """
    if effective_fps <= 0:
        return 3, 3
    minimum = max(2, math.ceil(effective_fps * MIN_VISIBLE_SECONDS))
    short = max(3, math.ceil(effective_fps * SHORT_TEXT_MIN_VISIBLE_SECONDS))
    return minimum, short


def _write_ocr_frames(
    path: Path,
    source_file: str,
    engine_name: str,
    frames: list[OCRFrame],
    details: dict[str, Any],
) -> None:
    segments = merge_frames(frames)
    media_type = details["media_type"]
    effective_fps = float(details.get("effective_fps") or 0)
    min_observations, short_text_min_observations = filter_thresholds(effective_fps)

    def useful(segment) -> bool:
        visible = [character for character in segment.text if character.isalnum()]
        if not visible or segment.confidence < 0.45:
            return False
        if len(visible) <= SHORT_TEXT_MAX_CHARACTERS and segment.observations < short_text_min_observations:
            return False
        if media_type == "video" and segment.observations < min_observations:
            # Preserve a long, high-confidence one-frame flash, but do not send
            # short single-frame texture/noise to the downstream model. It
            # remains present in raw frames and deduplicated_segments.
            if not (len(visible) >= 12 and segment.confidence >= 0.9):
                return False
        return True

    candidates = [replace(segment) for segment in segments if useful(segment)]

    def temporally_related(a, b) -> bool:
        if a.first_seen_seconds is None or b.first_seen_seconds is None:
            return True
        return (
            a.first_seen_seconds <= (b.last_seen_seconds or b.first_seen_seconds) + 2.0
            and b.first_seen_seconds <= (a.last_seen_seconds or a.first_seen_seconds) + 2.0
        )

    # Remove partial typewriter states and fragmented OCR when a fuller line
    # exists nearby in time. This is the final H/HE/HEL/HELL/HELLO safeguard,
    # including cases where the detector changed the box enough to split tracks.
    retained: list = []
    for index, segment in enumerate(candidates):
        key = _key(segment.text)
        subsumed = any(
            index != other_index
            and len(key) < len(other_key := _key(other.text))
            and len(key) >= 2
            and key in other_key
            and temporally_related(segment, other)
            for other_index, other in enumerate(candidates)
        )
        if not subsumed:
            retained.append(segment)

    # Cluster remaining near-identical OCR spellings and keep the strongest
    # temporal consensus. Do not alter the auditable deduplicated_segments.
    useful_segments: list = []
    for segment in retained:
        key = _key(segment.text)
        similar = next(
            (
                existing
                for existing in useful_segments
                if min(len(_key(existing.text)), len(key)) >= 4
                and ratio(_key(existing.text), key) >= 88
                and temporally_related(existing, segment)
            ),
            None,
        )
        if similar is None:
            useful_segments.append(segment)
            continue
        existing_rank = (similar.observations, similar.confidence, len(_key(similar.text)))
        new_rank = (segment.observations, segment.confidence, len(key))
        if new_rank > existing_rank:
            useful_segments[useful_segments.index(similar)] = segment
    payload = {
        "source_file": source_file,
        "engine": engine_name,
        **details,
        "frames": [frame.to_dict() for frame in frames],
        "deduplicated_segments": [segment.to_dict() for segment in segments],
        "llm_ready_segments": [segment.to_dict() for segment in useful_segments],
        "llm_ready_filter": {
            "minimum_confidence": 0.45,
            "reject_punctuation_only": True,
            "effective_fps": round(effective_fps, 3),
            "short_text_minimum_observations": short_text_min_observations,
            "video_minimum_observations": min_observations,
            "minimum_visible_seconds": MIN_VISIBLE_SECONDS,
            "short_text_minimum_visible_seconds": SHORT_TEXT_MIN_VISIBLE_SECONDS,
            "one_frame_exception": "at least 12 alphanumeric characters and confidence >= 0.9",
            "remove_temporally_related_substrings": True,
            "near_duplicate_similarity": 88,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_suffix(".txt").write_text(
        "\n".join(segment.text for segment in useful_segments) + ("\n" if useful_segments else ""), encoding="utf-8"
    )
    raw_text_path = path.with_name(path.name.removesuffix(".json") + ".raw.txt")
    raw_text_path.write_text(
        "\n".join(segment.text for segment in segments) + ("\n" if segments else ""), encoding="utf-8"
    )


def _write_ocr_result(path: Path, source: Path, engine: OCREngine, frames: list[OCRFrame], details: dict[str, Any]) -> None:
    _write_ocr_frames(path, source.name, engine.name, frames, details)


def rededuplicate_ocr_result(path: Path) -> None:
    """Rebuild compact text from stored frame OCR without rerunning a model."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    frames = [
        OCRFrame(
            time_seconds=frame.get("time_seconds"),
            frame_number=frame.get("frame_number"),
            lines=[
                OCRLine(
                    text=line["text"],
                    confidence=float(line["confidence"]),
                    bbox=tuple(line["bbox"]),
                )
                for line in frame.get("lines", [])
            ],
        )
        for frame in payload.get("frames", [])
    ]
    excluded = {
        "source_file",
        "engine",
        "frames",
        "deduplicated_segments",
        "llm_ready_segments",
        "llm_ready_filter",
    }
    details = {key: value for key, value in payload.items() if key not in excluded}
    # Results written before effective_fps existed OCR'd every decoded frame.
    if "effective_fps" not in details and details.get("media_type") == "video":
        details["effective_fps"] = float(details.get("source_fps") or 0)
    _write_ocr_frames(path, payload["source_file"], payload["engine"], frames, details)


def ocr_image(source: Path, output_json: Path, engine: OCREngine) -> None:
    with Image.open(source) as image:
        lines = engine.recognize(image)
    _write_ocr_result(
        output_json,
        source,
        engine,
        [lines_to_frame(lines, None, None)],
        {"media_type": "image", "frames_decoded": 1, "frames_ocrd": 1, **engine.details()},
    )


def ocr_images(
    sources: list[Path],
    output_jsons: list[Path],
    engine: OCREngine,
    batch_size: int = 8,
) -> None:
    """OCR still images in batches, which is much faster for neural engines."""
    if len(sources) != len(output_jsons):
        raise ValueError("sources and output_jsons must have the same length")
    for start in range(0, len(sources), batch_size):
        source_batch = sources[start : start + batch_size]
        output_batch = output_jsons[start : start + batch_size]
        images: list[Image.Image] = []
        for source in source_batch:
            with Image.open(source) as image:
                images.append(image.convert("RGB"))
        recognized = engine.recognize_batch(images)
        for source, output_json, lines in zip(source_batch, output_batch, recognized):
            _write_ocr_result(
                output_json,
                source,
                engine,
                [lines_to_frame(lines, None, None)],
                {"media_type": "image", "frames_decoded": 1, "frames_ocrd": 1, **engine.details()},
            )


def ocr_video(
    source: Path,
    output_json: Path,
    engine: OCREngine,
    mode: str = "sample",
    sample_fps: float = 3.0,
    max_height: int = 800,
    batch_size: int = 8,
) -> None:
    """OCR a video by sampling frames in time.

    On-screen text persists for seconds, so OCRing every frame re-reads the same
    words dozens of times. Sampling at 3 fps was measured to OCR 10.6% of frames
    while retaining 88.9% of substantial overlay text; what it drops is dominated
    by unstable single-frame noise. ``mode="all"`` keeps the exhaustive
    behaviour for regression comparisons.
    """
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"could not open video: {source}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    stride = 1 if mode == "all" else max(1, round(source_fps / sample_fps))
    frames: list[OCRFrame] = []
    pending_images: list[Image.Image] = []
    pending_metadata: list[tuple[float, int]] = []
    decoded = 0
    scale = 1.0

    def flush() -> None:
        if not pending_images:
            return
        recognized = engine.recognize_batch(pending_images)
        for lines, (time_seconds, frame_number) in zip(recognized, pending_metadata):
            frames.append(lines_to_frame(lines, time_seconds, frame_number))
        pending_images.clear()
        pending_metadata.clear()

    try:
        while True:
            # grab() advances without converting the frame to BGR; only sampled
            # frames pay for retrieve(). Measured 30s -> 13.8s over the corpus.
            if not capture.grab():
                break
            frame_number = decoded
            decoded += 1
            if frame_number % stride:
                continue
            ok, frame = capture.retrieve()
            if not ok:
                continue
            height = frame.shape[0]
            if max_height and height > max_height:
                scale = max_height / height
                frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pending_images.append(Image.fromarray(rgb))
            pending_metadata.append((frame_number / source_fps, frame_number))
            if len(pending_images) >= batch_size:
                flush()
        flush()
    finally:
        capture.release()

    duration = decoded / source_fps if source_fps else 0.0
    _write_ocr_result(
        output_json,
        source,
        engine,
        frames,
        {
            "media_type": "video",
            "video_mode": mode,
            "source_fps": source_fps,
            "sample_fps": sample_fps if mode != "all" else source_fps,
            "effective_fps": (len(frames) / duration) if duration else 0.0,
            "frame_stride": stride,
            "max_height": max_height,
            "frame_scale": round(scale, 4),
            "duration_seconds": round(duration, 3),
            "ocr_batch_size": batch_size,
            "frames_decoded": decoded,
            "frames_ocrd": len(frames),
            **engine.details(),
        },
    )
