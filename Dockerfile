# Portable model-ready image for any Linux host, amd64 or arm64. Surya and MLX
# remain installable project extras, but are not included here because the
# production choices are RapidOCR and faster-whisper.
FROM denoland/deno:bin-2.8.1 AS deno_bin


FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    INFO_TRIAGE_OCR_MODEL_DIR=/app/.ocr_models

# Resolve the large, stable dependency layer before copying application code.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY config.yaml ./
COPY src/ ./src/
COPY docker/ ./docker/
RUN uv sync --frozen --no-dev

# Runtime model downloads are deliberately forbidden. Preload and exercise the
# selected OCR and transcription models so architecture or download failures
# fail the image build instead of a future processing job.
RUN /app/.venv/bin/python docker/preload_models.py


FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV HF_HUB_OFFLINE=1 \
    DENO_NO_PROMPT=1 \
    DENO_NO_UPDATE_CHECK=1 \
    INFO_TRIAGE_OCR_MODEL_DIR=/app/.ocr_models \
    INFO_TRIAGE_OCR_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    PYTHONUNBUFFERED=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    PATH="/app/.venv/bin:$PATH"

# The user the container runs as: whoever owns ./data on the host. Compose passes
# INFO_TRIAGE_UID and INFO_TRIAGE_GID from .env here and to `user:`.
ARG APP_UID=1000
ARG APP_GID=1000

COPY --from=deno_bin /deno /usr/local/bin/deno
COPY --from=builder --chown=${APP_UID}:${APP_GID} /app /app
RUN mkdir -p /app/data && chown ${APP_UID}:${APP_GID} /app/data

USER ${APP_UID}:${APP_GID}
WORKDIR /app
VOLUME ["/app/data"]

CMD ["python", "-m", "info_triage"]
