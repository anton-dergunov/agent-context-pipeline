from __future__ import annotations

import json
import os
import html
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image
from rapidfuzz.fuzz import ratio

from .dedup import _key, lines_to_frame, merge_frames
from .models import OCRFrame, OCRLine


class OCREngine(ABC):
    name: str

    @abstractmethod
    def recognize(self, image: Image.Image) -> list[OCRLine]:
        raise NotImplementedError

    def recognize_batch(self, images: list[Image.Image]) -> list[list[OCRLine]]:
        return [self.recognize(image) for image in images]


class SuryaOCR(OCREngine):
    """High-quality, single-model OCR for 90+ languages."""

    name = "surya"

    def __init__(self, model_cache_dir: Path | None = None) -> None:
        # Surya reads settings at import time. Keep its downloaded weights local
        # to this project unless the caller has selected another cache directory.
        cache = model_cache_dir or Path(os.environ.get("INSTAGRAM_OCR_MODEL_DIR", ".ocr_models")).resolve()
        cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MODEL_CACHE_DIR", str(cache))

        from surya.detection import DetectionPredictor  # type: ignore[import-not-found]
        from surya.foundation import FoundationPredictor  # type: ignore[import-not-found]
        from surya.recognition import RecognitionPredictor  # type: ignore[import-not-found]

        foundation = FoundationPredictor()
        self.recognition = RecognitionPredictor(foundation)
        self.detection = DetectionPredictor()
        self.recognition.disable_tqdm = True
        self.detection.disable_tqdm = True

    @staticmethod
    def _lines(result: Any) -> list[OCRLine]:
        image_width = float(result.image_bbox[2])
        image_height = float(result.image_bbox[3])
        lines: list[OCRLine] = []
        for line in result.text_lines:
            x1, y1, x2, y2 = map(float, line.bbox)
            # Surya preserves presentational markup such as <b>; downstream
            # summarization needs the visible characters, not HTML styling.
            visible_text = html.unescape(re.sub(r"<[^>]+>", "", line.text)).strip()
            lines.append(
                OCRLine(
                    text=visible_text,
                    confidence=float(line.confidence or 0),
                    bbox=(
                        x1 / image_width,
                        1 - y2 / image_height,
                        (x2 - x1) / image_width,
                        (y2 - y1) / image_height,
                    ),
                )
            )
        return lines

    def recognize(self, image: Image.Image) -> list[OCRLine]:
        return self.recognize_batch([image])[0]

    def recognize_batch(self, images: list[Image.Image]) -> list[list[OCRLine]]:
        results = self.recognition(
            images,
            det_predictor=self.detection,
            sort_lines=True,
            math_mode=False,
            return_words=True,
            drop_repeated_text=True,
        )
        return [self._lines(result) for result in results]


class VisionOCR(OCREngine):
    name = "apple-vision"

    def __init__(self) -> None:
        import objc  # type: ignore[import-not-found]
        import Quartz  # type: ignore[import-not-found]
        import Vision  # type: ignore[import-not-found]
        from Foundation import NSData  # type: ignore[import-not-found]

        self.objc = objc
        self.Quartz = Quartz
        self.Vision = Vision
        self.NSData = NSData

    def recognize(self, image: Image.Image) -> list[OCRLine]:
        # Thousands of video frames otherwise leave Objective-C autoreleased
        # Vision objects alive until process exit and can exhaust memory.
        with self.objc.autorelease_pool():
            return self._recognize(image)

    def _recognize(self, image: Image.Image) -> list[OCRLine]:
        import io

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=95)
        raw = buffer.getvalue()
        data = self.NSData.dataWithBytes_length_(raw, len(raw))
        source = self.Quartz.CGImageSourceCreateWithData(data, None)
        cg_image = self.Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)

        request = self.Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(self.Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        if hasattr(request, "setAutomaticallyDetectsLanguage_"):
            request.setAutomaticallyDetectsLanguage_(True)
        handler = self.Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, {})
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Apple Vision OCR failed: {error}")

        lines: list[OCRLine] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            box = observation.boundingBox()
            lines.append(
                OCRLine(
                    text=str(candidate.string()),
                    confidence=float(candidate.confidence()),
                    bbox=(float(box.origin.x), float(box.origin.y), float(box.size.width), float(box.size.height)),
                )
            )
        return lines


class TesseractOCR(OCREngine):
    name = "tesseract"

    # Script models cover far more languages without having to know the language in advance.
    DEFAULT_SCRIPTS = "script/Latin+script/Cyrillic+script/Arabic+script/HanS+script/HanT+script/Japanese+script/Hangul+script/Devanagari"

    def __init__(self, languages: str | None = None) -> None:
        if not shutil.which("tesseract"):
            raise RuntimeError("tesseract is not installed")
        self.languages = languages or self.DEFAULT_SCRIPTS

    def recognize(self, image: Image.Image) -> list[OCRLine]:
        with tempfile.NamedTemporaryFile(suffix=".png") as temp:
            image.save(temp.name)
            command = [
                "tesseract", temp.name, "stdout", "-l", self.languages, "--psm", "11", "tsv",
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
        rows = result.stdout.splitlines()
        if not rows:
            return []
        width, height = image.size
        lines: list[OCRLine] = []
        for row in rows[1:]:
            fields = row.split("\t")
            if len(fields) < 12 or not fields[11].strip():
                continue
            try:
                confidence = float(fields[10]) / 100.0
                left, top, box_width, box_height = map(int, fields[6:10])
            except ValueError:
                continue
            if confidence < 0:
                continue
            lines.append(
                OCRLine(
                    text=fields[11].strip(),
                    confidence=confidence,
                    bbox=(left / width, 1 - (top + box_height) / height, box_width / width, box_height / height),
                )
            )
        return lines


def make_engine(
    name: str = "auto",
    tesseract_languages: str | None = None,
    model_cache_dir: Path | None = None,
) -> OCREngine:
    errors: list[str] = []
    if name in {"auto", "surya"}:
        try:
            return SuryaOCR(model_cache_dir)
        except Exception as exc:
            errors.append(f"Surya: {exc}")
            if name == "surya":
                raise
    if name in {"auto", "vision"}:
        try:
            return VisionOCR()
        except Exception as exc:
            errors.append(f"Vision: {exc}")
            if name == "vision":
                raise
    if name in {"auto", "tesseract"}:
        try:
            return TesseractOCR(tesseract_languages)
        except Exception as exc:
            errors.append(f"Tesseract: {exc}")
    raise RuntimeError("no OCR engine is available (" + "; ".join(errors) + ")")


def _write_ocr_frames(
    path: Path,
    source_file: str,
    engine_name: str,
    frames: list[OCRFrame],
    details: dict[str, Any],
) -> None:
    segments = merge_frames(frames)
    media_type = details["media_type"]
    source_fps = float(details.get("source_fps") or 0)

    def useful(segment) -> bool:
        visible = [character for character in segment.text if character.isalnum()]
        if not visible or segment.confidence < 0.45:
            return False
        if len(visible) <= 3 and segment.observations < max(3, round(source_fps * 0.25)):
            return False
        if media_type == "video" and segment.observations < 3:
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
            "short_text_minimum_observations": max(3, round(source_fps * 0.25)),
            "video_minimum_observations": 3,
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
    _write_ocr_frames(path, payload["source_file"], payload["engine"], frames, details)


def ocr_image(source: Path, output_json: Path, engine: OCREngine) -> None:
    with Image.open(source) as image:
        lines = engine.recognize(image)
    _write_ocr_result(
        output_json,
        source,
        engine,
        [lines_to_frame(lines, None, None)],
        {"media_type": "image", "frames_decoded": 1, "frames_ocrd": 1},
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
                {"media_type": "image", "frames_decoded": 1, "frames_ocrd": 1},
            )


def _frame_signature(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)


def ocr_video(
    source: Path,
    output_json: Path,
    engine: OCREngine,
    mode: str = "adaptive",
    sample_fps: float = 2.0,
    change_threshold: float = 11.0,
    batch_size: int = 8,
) -> None:
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"could not open video: {source}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    periodic_stride = max(1, round(source_fps / sample_fps))
    frames: list[OCRFrame] = []
    pending_images: list[Image.Image] = []
    pending_metadata: list[tuple[float, int]] = []
    decoded = 0
    previous_signature: np.ndarray | None = None

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
            ok, frame = capture.read()
            if not ok:
                break
            frame_number = decoded
            decoded += 1
            signature = _frame_signature(frame)
            changed = previous_signature is None or float(np.mean(cv2.absdiff(signature, previous_signature))) >= change_threshold
            should_ocr = mode == "all" or frame_number % periodic_stride == 0 or changed
            previous_signature = signature
            if not should_ocr:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pending_images.append(Image.fromarray(rgb))
            pending_metadata.append((frame_number / source_fps, frame_number))
            if len(pending_images) >= batch_size:
                flush()
        flush()
    finally:
        capture.release()
    _write_ocr_result(
        output_json,
        source,
        engine,
        frames,
        {
            "media_type": "video",
            "video_mode": mode,
            "source_fps": source_fps,
            "sample_fps": sample_fps,
            "change_threshold": change_threshold,
            "ocr_batch_size": batch_size,
            "frames_decoded": decoded,
            "frames_ocrd": len(frames),
        },
    )
