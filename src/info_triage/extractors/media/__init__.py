"""Shared local media OCR and speech-transcription utilities."""

# Configure native thread pools before numpy/OpenCV/ONNX Runtime are imported.
from . import runtime as _runtime

_runtime.configure_threads()
