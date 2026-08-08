#!/usr/bin/env bash

set -euo pipefail

REMOTE="server"
REMOTE_DIR="/volume1/docker/hello"
URL="http://192.168.1.10:8000"

echo "==> Copying files to NAS"

rsync -az --delete \
    --exclude '.git/' \
    --exclude '.env' \
    --exclude 'data/' \
    --exclude 'logs/' \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    ./ "${REMOTE}:${REMOTE_DIR}/"

echo "==> Building and restarting container"

ssh "$REMOTE" \
    'sudo -n /usr/local/sbin/deploy-container hello'

echo "==> Waiting for service"

sleep 3

echo "==> Health check"

curl --fail --silent --show-error "$URL"

echo
echo "==> Deployment complete"
