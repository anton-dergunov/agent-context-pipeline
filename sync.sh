#!/usr/bin/env bash

set -euo pipefail

REMOTE="server"
REMOTE_INBOX="/volume1/docker/info-triage/data/inbox"
LOCAL_INBOX="${HOME}/info-triage-inbox"
STATE_DIR="${HOME}/.local/state/info-triage"
MANIFEST="${STATE_DIR}/delivered-items"

if [[ -e "$MANIFEST" && ! -d "$LOCAL_INBOX" ]]; then
    echo "Local inbox is missing: $LOCAL_INBOX" >&2
    echo "Refusing to synchronize because its contents may have been deleted accidentally." >&2
    exit 1
fi

mkdir -p "$LOCAL_INBOX" "$STATE_DIR"

SYNC_TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$SYNC_TEMP_DIR"' EXIT
REMOTE_ITEMS="$SYNC_TEMP_DIR/remote-items"
UPDATED_MANIFEST="$SYNC_TEMP_DIR/delivered-items"

echo "==> Reading NAS inbox"

ssh "$REMOTE" \
    "test -d '$REMOTE_INBOX' && for item_path in '$REMOTE_INBOX'/*; do [ -d \"\$item_path\" ] && basename \"\$item_path\"; done" \
    | sort -u >"$REMOTE_ITEMS"

while IFS= read -r item; do
    [[ -z "$item" ]] && continue
    if [[ ! "$item" =~ ^-?[0-9]+_[0-9]+$ ]]; then
        echo "Unexpected NAS inbox directory: $item" >&2
        echo "Refusing to synchronize an invalid item name." >&2
        exit 1
    fi
done <"$REMOTE_ITEMS"

touch "$MANIFEST"

echo "==> Removing items processed on the laptop"

while IFS= read -r item; do
    [[ -z "$item" ]] && continue
    if grep -Fqx -- "$item" "$MANIFEST" && [[ ! -d "$LOCAL_INBOX/$item" ]]; then
        ssh "$REMOTE" "rm -rf -- '$REMOTE_INBOX/$item'"
        echo "Removed processed item: $item"
    fi
done <"$REMOTE_ITEMS"

echo "==> Downloading new and edited items"

rsync -azc \
    "${REMOTE}:${REMOTE_INBOX}/" \
    "${LOCAL_INBOX}/"

{
    sed '/^$/d' "$MANIFEST"
    sed '/^$/d' "$REMOTE_ITEMS"
} | sort -u >"$UPDATED_MANIFEST"
mv "$UPDATED_MANIFEST" "$MANIFEST"

echo "==> Synchronization complete"
echo "Laptop inbox: $LOCAL_INBOX"
