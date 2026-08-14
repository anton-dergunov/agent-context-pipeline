#!/usr/bin/env bash

set -euo pipefail

REMOTE="server"
REMOTE_DIR="/volume1/docker/info-triage"
URL="http://192.168.1.10:8000/health"

if [[ ! -f .env ]]; then
    echo "Missing .env with Telegram credentials" >&2
    exit 1
fi

echo "==> Preparing deployment directory"

ssh "$REMOTE" \
    "mkdir -p '$REMOTE_DIR/data/staging' '$REMOTE_DIR/data/inbox'"

echo "==> Copying files to NAS"

rsync -az --delete \
    --exclude '.git/' \
    --exclude '.env' \
    --exclude 'data/' \
    --exclude 'logs/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    ./ "${REMOTE}:${REMOTE_DIR}/"

rsync -az .env "${REMOTE}:${REMOTE_DIR}/.env"
ssh "$REMOTE" "chmod 600 '$REMOTE_DIR/.env'"

echo "==> Building and restarting container"

ssh "$REMOTE" \
    'sudo -n /usr/local/sbin/deploy-container info-triage'

echo "==> Health check"

curl --fail --silent --show-error \
    --connect-timeout 2 \
    --max-time 5 \
    --retry 60 \
    --retry-all-errors \
    --retry-delay 1 \
    --retry-max-time 60 \
    "$URL"

echo
echo "==> Deployment complete"
