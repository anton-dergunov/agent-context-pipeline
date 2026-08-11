"""Local Instagram media and text extraction utilities."""

# Must run before numpy/OpenCV/ONNX Runtime are imported anywhere in the process,
# because those libraries size their thread pools at import time.
from . import runtime as _runtime

_runtime.configure_threads()

__version__ = "0.1.0"
