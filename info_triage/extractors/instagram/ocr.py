"""Compatibility imports for the shared media OCR pipeline."""

from info_triage.extractors.media.ocr import (
    OCREngine,
    filter_thresholds,
    make_engine,
    ocr_image,
    ocr_images,
    ocr_video,
    rededuplicate_ocr_result,
)

__all__ = [
    "OCREngine",
    "filter_thresholds",
    "make_engine",
    "ocr_image",
    "ocr_images",
    "ocr_video",
    "rededuplicate_ocr_result",
]
