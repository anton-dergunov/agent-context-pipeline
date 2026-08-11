"""OCR backends for Instagram media.

Every engine returns :class:`OCRLine` with a bbox normalized to
``(x, y_from_bottom, width, height)`` in 0..1, so the deduplication layer is
independent of the backend and of the resolution frames were OCR'd at.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from PIL import Image

from .models import OCRLine
from .runtime import resolve_threads

# Recognition models are grouped by script and no single model covers both
# Chinese and Cyrillic. The Cyrillic model's charset was measured to cover
# Latin, Cyrillic and Spanish accents at 100%, so one model serves
# English/Spanish/Russian with no per-frame language guessing.
#
# Uncomment "ch" to also recognize Chinese overlay text. With more than one
# entry the engine calibrates on the first frames containing text and then
# keeps the highest-scoring script for the rest of the file.
DEFAULT_SCRIPTS: tuple[str, ...] = (
    "cyrillic",
    # "ch",
)

#: Frames containing text to score before locking in a script.
CALIBRATION_FRAMES = 8


class OCREngine(ABC):
    name: str

    @abstractmethod
    def recognize(self, image: Image.Image) -> list[OCRLine]:
        raise NotImplementedError

    def recognize_batch(self, images: list[Image.Image]) -> list[list[OCRLine]]:
        return [self.recognize(image) for image in images]

    def details(self) -> dict[str, Any]:
        """Engine-specific fields recorded in the OCR JSON."""
        return {}


class SuryaOCR(OCREngine):
    """High-quality, single-model OCR for 90+ languages."""

    name = "surya"

    def __init__(self, model_cache_dir: Path | None = None) -> None:
        # Surya reads settings at import time. Keep its downloaded weights local
        # to this project unless the caller has selected another cache directory.
        cache = (
            model_cache_dir
            or Path(os.environ.get("INSTAGRAM_OCR_MODEL_DIR", ".ocr_models")).resolve()
        )
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
                    bbox=(
                        float(box.origin.x),
                        float(box.origin.y),
                        float(box.size.width),
                        float(box.size.height),
                    ),
                )
            )
        return lines


class RapidOCREngine(OCREngine):
    """PP-OCR detection + recognition through ONNX Runtime.

    Portable across x86-64 and ARM, and the only engine here that is practical
    on a NAS or a Raspberry Pi: Surya was measured at 8.3 s/frame and Tesseract
    produced unusable output on video frames.
    """

    name = "rapidocr"

    def __init__(
        self,
        scripts: tuple[str, ...] | None = None,
        threads: int | None = None,
        model_dir: Path | None = None,
        detector_side: int = 960,
        text_score: float = 0.5,
    ) -> None:
        from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

        self.threads = resolve_threads(threads)
        self.scripts = tuple(scripts or DEFAULT_SCRIPTS)
        if not self.scripts:
            raise ValueError("at least one recognition script is required")
        self.detector_side = detector_side
        self._selected: str | None = self.scripts[0] if len(self.scripts) == 1 else None
        self._scores: dict[str, list[float]] = {script: [] for script in self.scripts}
        self._calibrated_frames = 0

        base: dict[str, Any] = {
            # Overlay text is not rotated; the orientation classifier is pure cost.
            "Global.use_cls": False,
            "Global.text_score": text_score,
            "Global.log_level": "error",
            "EngineConfig.onnxruntime.intra_op_num_threads": self.threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": LangDet.CH,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.model_type": ModelType.MOBILE,
            "Det.limit_side_len": detector_side,
            # "max" caps the long side. The shipped default is "min", which
            # scales the *short* side up to 736 and enlarges an already
            # downscaled frame beyond its source resolution.
            "Det.limit_type": "max",
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
        }
        if model_dir is not None:
            Path(model_dir).mkdir(parents=True, exist_ok=True)
            base["Global.model_root_dir"] = str(model_dir)

        self._engines: dict[str, Any] = {}
        for script in self.scripts:
            params = dict(base)
            params["Rec.lang_type"] = LangRec(script)
            self._engines[script] = RapidOCR(params=params)

    @staticmethod
    def _lines(result: Any, width: int, height: int) -> list[OCRLine]:
        lines: list[OCRLine] = []
        boxes = result.boxes if result.boxes is not None else []
        texts = result.txts or []
        scores = result.scores or []
        for box, text, score in zip(boxes, texts, scores):
            cleaned = str(text).strip()
            if not cleaned:
                continue
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            lines.append(
                OCRLine(
                    text=cleaned,
                    confidence=float(score),
                    # PP-OCR reports pixels with y growing downward; the project
                    # convention is normalized with y growing upward.
                    bbox=(
                        x0 / width,
                        1 - y1 / height,
                        (x1 - x0) / width,
                        (y1 - y0) / height,
                    ),
                )
            )
        return lines

    @staticmethod
    def _mean_confidence(result: Any) -> float | None:
        scores = [float(value) for value in (result.scores or [])]
        return sum(scores) / len(scores) if scores else None

    def recognize(self, image: Image.Image) -> list[OCRLine]:
        import numpy as np

        frame = np.asarray(image.convert("RGB"))[:, :, ::-1]  # RGB -> BGR
        height, width = frame.shape[:2]

        if self._selected is not None:
            result = self._engines[self._selected](frame)
            return self._lines(result, width, height)

        # Calibration: score every candidate script on this frame, use the best
        # result now, and lock in the winner once enough frames have had text.
        best_result: Any = None
        best_score = -1.0
        saw_text = False
        for script, engine in self._engines.items():
            result = engine(frame)
            mean = self._mean_confidence(result)
            if mean is None:
                continue
            saw_text = True
            self._scores[script].append(mean)
            if mean > best_score:
                best_score, best_result = mean, result

        if saw_text:
            self._calibrated_frames += 1
            if self._calibrated_frames >= CALIBRATION_FRAMES:
                self._selected = max(
                    self.scripts,
                    key=lambda script: (
                        sum(self._scores[script]) / len(self._scores[script])
                        if self._scores[script]
                        else 0.0
                    ),
                )

        if best_result is None:
            return []
        return self._lines(best_result, width, height)

    def script_scores(self) -> dict[str, float]:
        return {
            script: round(sum(values) / len(values), 4)
            for script, values in self._scores.items()
            if values
        }

    def details(self) -> dict[str, Any]:
        return {
            "rec_scripts_enabled": list(self.scripts),
            "rec_script": self._selected,
            "rec_script_scores": self.script_scores(),
            "onnx_threads": self.threads,
            "detector_side": self.detector_side,
        }


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
                "tesseract",
                temp.name,
                "stdout",
                "-l",
                self.languages,
                "--psm",
                "11",
                "tsv",
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
                    bbox=(
                        left / width,
                        1 - (top + box_height) / height,
                        box_width / width,
                        box_height / height,
                    ),
                )
            )
        return lines


def make_engine(
    name: str = "auto",
    tesseract_languages: str | None = None,
    model_cache_dir: Path | None = None,
    scripts: tuple[str, ...] | None = None,
    threads: int | None = None,
) -> OCREngine:
    errors: list[str] = []
    if name in {"auto", "rapidocr"}:
        try:
            return RapidOCREngine(scripts=scripts, threads=threads, model_dir=model_cache_dir)
        except Exception as exc:
            errors.append(f"RapidOCR: {exc}")
            if name == "rapidocr":
                raise
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
            if name == "tesseract":
                raise
    raise RuntimeError("no OCR engine is available (" + "; ".join(errors) + ")")
