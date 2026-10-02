"""Compatibility imports for the shared OCR engines."""

from info_triage.extractors.media.engines import (
    CALIBRATION_FRAMES,
    DEFAULT_SCRIPTS,
    OCREngine,
    RapidOCREngine,
    SuryaOCR,
    TesseractOCR,
    VisionOCR,
    make_engine,
)

__all__ = [
    "CALIBRATION_FRAMES",
    "DEFAULT_SCRIPTS",
    "OCREngine",
    "RapidOCREngine",
    "SuryaOCR",
    "TesseractOCR",
    "VisionOCR",
    "make_engine",
]
