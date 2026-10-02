#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=8000

if [[ ! -f .env ]]; then
    echo "Missing .env: copy .env.example to .env and fill it in" >&2
    exit 1
fi
if [[ ! -f config.yaml ]]; then
    echo "Missing config.yaml: copy config.example.yaml to config.yaml" >&2
    exit 1
fi

# One setting: the real environment first, then .env. Read key by key and never
# sourced, because .env also holds tokens, and a token is not shell-safe.
env_value() {
    local key="$1" value
    if value="$(printenv "$key")"; then
        printf '%s' "$value"
        return
    fi
    value="$(grep -E "^${key}=" .env | tail -n 1 || true)"
    value="${value#*=}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    printf '%s' "$value"
}

SERVER="$(env_value INFO_TRIAGE_SERVER)"
SERVER_DIR="$(env_value INFO_TRIAGE_SERVER_DIR)"
DEPLOY_COMMAND="$(env_value INFO_TRIAGE_DEPLOY_COMMAND)"

if [[ -z "$SERVER" ]]; then
    echo "==> No INFO_TRIAGE_SERVER set: building and starting the container on this machine"

    # Created here so that it belongs to this user and not to root, which is who
    # Docker would create a missing bind mount as.
    mkdir -p data
    if [[ -n "$DEPLOY_COMMAND" ]]; then
        bash -c "$DEPLOY_COMMAND"
    else
        docker compose up -d --build
    fi
    HOST="localhost"
else
    if [[ -z "$SERVER_DIR" ]]; then
        echo "INFO_TRIAGE_SERVER_DIR is not set in .env: the project directory on $SERVER" >&2
        exit 1
    fi

    echo "==> Preparing deployment directory"

    ssh "$SERVER" "mkdir -p '$SERVER_DIR/data'"

    echo "==> Copying files to $SERVER"

    # Never --delete-excluded: data/ and .env are excluded precisely so that the
    # mirror cannot remove them from the server.
    rsync -az --delete \
        --exclude '.git/' \
        --exclude '.env' \
        --exclude 'data/' \
        --exclude '.venv/' \
        --exclude '__pycache__/' \
        --exclude '.DS_Store' \
        --exclude '.pytest_cache/' \
        --exclude '.ruff_cache/' \
        --exclude '.uv-cache/' \
        --exclude '.ocr_models/' \
        --exclude '.whisper_models/' \
        --exclude '.bench_ocr/' \
        --exclude '.bench_transcription/' \
        --exclude 'data_for_analysis/' \
        --exclude '*_output/' \
        --exclude 'url_title_audit/' \
        ./ "${SERVER}:${SERVER_DIR}/"

    rsync -az .env "${SERVER}:${SERVER_DIR}/.env"
    ssh "$SERVER" "chmod 600 '$SERVER_DIR/.env'"

    echo "==> Building and restarting container"

    ssh "$SERVER" "${DEPLOY_COMMAND:-cd '$SERVER_DIR' && docker compose up -d --build}"

    # The address lives in ~/.ssh/config with the rest of the alias, when it is one.
    HOST="$(ssh -G "$SERVER" | awk '$1 == "hostname" { print $2 }')"
    HOST="${HOST:-$SERVER}"
fi

echo "==> Health check"

URL="http://${HOST}:${PORT}/health"

health_check_status=0
health_check_output=$(curl --fail --silent --show-error \
    --connect-timeout 2 \
    --max-time 5 \
    --retry 60 \
    --retry-all-errors \
    --retry-delay 1 \
    --retry-max-time 60 \
    "$URL" 2>&1) || health_check_status=$?

if [[ "$health_check_status" -ne 0 ]]; then
    printf '%s\n' "$health_check_output" >&2
    exit "$health_check_status"
fi

printf '%s\n' "$health_check_output"
echo "==> Deployment complete"
