# Portable image for the Synology NAS (linux/amd64) and Raspberry Pi (linux/arm64).
#
# Deliberately excludes the "surya" extra: PyTorch would add ~2-3 GB and Surya
# was measured at 8.3 s/frame, unusable for video on either machine. The ONNX
# engine covers English, Spanish and Russian from a single 8 MB recognition
# model.
#
# Build for the NAS from an ARM Mac with:
#   docker buildx build --platform linux/amd64 -t instagram-extractor:latest --load .

FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    INSTAGRAM_OCR_MODEL_DIR=/app/.ocr_models \
    INSTAGRAM_TRANSCRIPTION_MODEL_CACHE_DIR=/app/.whisper_models \
    INSTAGRAM_TRANSCRIPTION_MODEL=small

# Dependencies first so code edits do not invalidate the heavy layer.
COPY pyproject.toml uv.lock README.instagram.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY instagram_extractor/ ./instagram_extractor/
# The root Python distribution now contains both extractor packages. The image
# still exposes only the Instagram entry point, but Hatch needs both package
# directories present when it builds the installed project.
COPY linkedin_extractor/ ./linkedin_extractor/
COPY docker/ ./docker/
COPY tools/ ./tools/
RUN uv sync --frozen --no-dev

# Bake the models in. The container must never download at runtime: the NAS may
# be offline and a scheduled job should not depend on a CDN in China.
RUN /app/.venv/bin/python docker/preload_models.py


FROM python:3.12-slim-bookworm

# libgomp1 is OpenMP, required by ONNX Runtime. No ffmpeg and no GUI libraries:
# opencv-python-headless bundles its own decoders, which is why pyproject
# overrides away the opencv-python that rapidocr would otherwise pull in.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 extractor \
    && mkdir -p /data \
    && chown extractor:extractor /data

# Thread pinning. ONNX Runtime, CTranslate2, and OpenMP size pools from the host
# CPU count and ignore the cgroup quota, so a container limited to one CPU would
# otherwise start a thread per host core and lose the time to context
# switching. Keep this in step with the `cpus` limit in docker-compose.yml.
ENV INSTAGRAM_OCR_THREADS=1 \
    INSTAGRAM_TRANSCRIPTION_THREADS=1 \
    INSTAGRAM_TRANSCRIPTION_BACKEND=faster-whisper \
    INSTAGRAM_TRANSCRIPTION_MODEL=small \
    INSTAGRAM_TRANSCRIPTION_MODEL_CACHE_DIR=/app/.whisper_models \
    HF_HUB_OFFLINE=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    INSTAGRAM_OCR_MODEL_DIR=/app/.ocr_models \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

COPY --from=builder --chown=extractor:extractor /app /app

USER extractor
VOLUME ["/data"]
WORKDIR /data

# The venv entry point directly: `uv run` would re-resolve and reinstall the
# project on every start, which also needs a writable /app.
ENTRYPOINT ["instagram-extract"]
CMD ["--help"]
