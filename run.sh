#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f .env ]]; then
    echo "Missing .env: copy .env.example to .env and fill it in" >&2
    exit 1
fi
if [[ ! -f config.yaml ]]; then
    echo "Missing config.yaml: copy config.example.yaml to config.yaml" >&2
    exit 1
fi

docker compose up --build
