"""Download the OCR and selected Whisper models at image build time.

The container must not reach ModelScope on first run: the NAS may be offline,
the CDN is slow from Europe, and a runtime download turns a scheduled job into a
network dependency. Building the engine once forces every model it needs into
the image.
"""

from __future__ import annotations

import os
from pathlib import Path

from info_triage.config import VoiceTranscriptionConfig, load_config
from info_triage.extractors.media.engines import (
    DEFAULT_SCRIPTS,
    RapidOCREngine,
)
from info_triage.extractors.media.transcription import (
    download_faster_whisper_model,
)

model_dir = Path(
    os.environ.get("INFO_TRIAGE_OCR_MODEL_DIR")
    or os.environ.get("INSTAGRAM_OCR_MODEL_DIR", "/app/.ocr_models")
)
scripts = tuple(os.environ.get("INSTAGRAM_OCR_SCRIPTS", ",".join(DEFAULT_SCRIPTS)).split(","))

print(f"preloading RapidOCR models for scripts={scripts} into {model_dir}")
engine = RapidOCREngine(scripts=scripts, threads=1, model_dir=model_dir)

# Exercise the graph once so a broken download fails the build, not the first run.
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

engine.recognize(Image.fromarray(np.zeros((320, 320, 3), dtype=np.uint8)))

total = sum(p.stat().st_size for p in model_dir.rglob("*") if p.is_file())
print(f"preloaded {total / 1e6:.1f} MB of models")

# The reviewed portable production choice: multilingual Whisper small on CPU
# with int8 weights. Store it in the same layout the runtime resolver expects.
from faster_whisper import WhisperModel  # noqa: E402

daemon_config = load_config(Path(os.environ.get("INFO_TRIAGE_CONFIG", "/app/config.yaml")))
# Any route that transcribes needs the model in the image, and they all share one.
voice_config = next(
    (
        step
        for route in daemon_config.routes
        for step in route.steps
        if isinstance(step, VoiceTranscriptionConfig)
    ),
    None,
)
if voice_config is None:
    print("voice transcription disabled; skipping Whisper model preload")
    raise SystemExit(0)
if voice_config.backend != "faster-whisper":
    raise RuntimeError("the portable Docker image requires the faster-whisper backend")

whisper_cache = voice_config.model_cache_dir
whisper_model = voice_config.model
whisper_path = whisper_cache / whisper_model
print(f"preloading faster-whisper {whisper_model} into {whisper_path}")
download_faster_whisper_model(whisper_model, whisper_cache)

# Loading validates both architecture-specific CTranslate2 wheels and the
# downloaded model during each amd64/arm64 image build.
WhisperModel(
    str(whisper_path),
    device="cpu",
    compute_type="int8",
    cpu_threads=1,
    num_workers=1,
    local_files_only=True,
)
whisper_total = sum(p.stat().st_size for p in whisper_path.rglob("*") if p.is_file())
print(f"preloaded {whisper_total / 1e6:.1f} MB of Whisper files")
