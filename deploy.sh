#!/usr/bin/env bash

set -euo pipefail

REMOTE="server"
REMOTE_DIR="/volume1/docker/info-triage"
PORT=8000

if [[ ! -f .env ]]; then
    echo "Missing .env with Telegram credentials" >&2
    exit 1
fi

echo "==> Preparing deployment directory"

# Written out rather than brace-expanded: the Synology remote shell is busybox.
ssh "$REMOTE" \
    "mkdir -p '$REMOTE_DIR/data/staging/info' '$REMOTE_DIR/data/inbox/info' \
              '$REMOTE_DIR/data/staging/job' '$REMOTE_DIR/data/inbox/job' \
              '$REMOTE_DIR/data/staging/clip' '$REMOTE_DIR/data/inbox/clip' \
              '$REMOTE_DIR/data/staging/lang' '$REMOTE_DIR/data/inbox/lang'"

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

# The NAS address lives in ~/.ssh/config with the rest of the "$REMOTE" alias.
HOST="$(ssh -G "$REMOTE" | awk '$1 == "hostname" { print $2 }')"
URL="http://${HOST:-$REMOTE}:${PORT}/health"

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
