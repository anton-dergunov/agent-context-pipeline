"""Instagram extractor thread and platform resolution.

Imported before numpy/OpenCV/ONNX Runtime so the threading environment variables
are in place by the time those libraries read them. ONNX Runtime and OpenMP size
their pools from the *host* CPU count and ignore the cgroup quota, so a container
limited with ``--cpus`` would otherwise start far more threads than it can run and
lose time to context switching.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

#: Set by every library that reads a thread count from the environment.
THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

THREAD_ENV = "INSTAGRAM_OCR_THREADS"

_CGROUP_V2 = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def cgroup_cpu_limit() -> float | None:
    """Effective CPU allowance from the cgroup, or None when unlimited/absent."""
    try:
        raw = _CGROUP_V2.read_text(encoding="utf-8").split()
        if len(raw) == 2 and raw[0] != "max":
            quota, period = int(raw[0]), int(raw[1])
            if quota > 0 and period > 0:
                return quota / period
        elif raw and raw[0] == "max":
            return None
    except (OSError, ValueError):
        pass

    quota = _read_int(_CGROUP_V1_QUOTA)
    period = _read_int(_CGROUP_V1_PERIOD)
    if quota and period and quota > 0 and period > 0:
        return quota / period
    return None


def resolve_threads(requested: int | None = None) -> int:
    """Thread count to use: explicit request, then env, then cgroup, then CPUs."""
    if requested is not None and requested > 0:
        return requested

    from_env = os.environ.get(THREAD_ENV)
    if from_env:
        try:
            value = int(from_env)
            if value > 0:
                return value
        except ValueError:
            pass

    limit = cgroup_cpu_limit()
    if limit is not None:
        return max(1, int(limit))

    return max(1, os.cpu_count() or 1)


def configure_threads(threads: int | None = None) -> int:
    """Publish the thread count into the environment. Call before heavy imports."""
    count = resolve_threads(threads)
    for name in THREAD_ENV_VARS:
        os.environ.setdefault(name, str(count))
    return count


def apply_runtime_threads(threads: int) -> None:
    """Apply the thread count to libraries that must be told at runtime."""
    try:
        import cv2

        cv2.setNumThreads(threads)
    except Exception:
        # OpenCV is optional at import time; the OCR paths import it themselves.
        pass


def platform_defaults() -> tuple[str, str]:
    """(image engine, video engine) for ``--ocr-engine best`` on this machine.

    Apple Vision only exists on macOS, so ``best`` must not resolve to it
    elsewhere. On Linux both paths use the ONNX engine, which keeps PyTorch out
    of the container image entirely.
    """
    if platform.system() == "Darwin":
        return "surya", "vision"
    return "rapidocr", "rapidocr"


def describe() -> dict[str, object]:
    """Machine description recorded alongside benchmark results."""
    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "cgroup_cpu_limit": cgroup_cpu_limit(),
        "threads": resolve_threads(),
        "python": platform.python_version(),
    }
