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
REMOTE_METADATA="$SYNC_TEMP_DIR/remote-metadata"
REMOTE_REVISIONS="$SYNC_TEMP_DIR/remote-revisions"
NAME_MAP="$SYNC_TEMP_DIR/name-map"
NORMALIZED_MANIFEST="$SYNC_TEMP_DIR/normalized-manifest"
UPDATED_MANIFEST="$SYNC_TEMP_DIR/delivered-items"

mkdir -p "$REMOTE_METADATA"
touch "$MANIFEST"

echo "==> Reading NAS item revisions"

ssh "$REMOTE" "test -d '$REMOTE_INBOX'"
rsync -az \
    --include '*/' \
    --include 'metadata.json' \
    --exclude '*' \
    "${REMOTE}:${REMOTE_INBOX}/" \
    "${REMOTE_METADATA}/"

shopt -s nullglob
for item_path in "$REMOTE_METADATA"/*; do
    [[ -d "$item_path" ]] || continue
    item="$(basename "$item_path")"
    if [[ ! "$item" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]+$ && ! "$item" =~ ^-?[0-9]+_[0-9]+$ ]]; then
        echo "Unexpected NAS inbox directory: $item" >&2
        exit 1
    fi
    if [[ ! -f "$item_path/metadata.json" ]]; then
        echo "Missing metadata.json for NAS item: $item" >&2
        exit 1
    fi
    revision="$(sed -nE 's/^[[:space:]]*"revision":[[:space:]]*([0-9]+),?[[:space:]]*$/\1/p' "$item_path/metadata.json")"
    if [[ -z "$revision" ]]; then
        revision=1
    fi
    message_id="$(sed -nE 's/^[[:space:]]*"message_id":[[:space:]]*([0-9]+),?[[:space:]]*$/\1/p' "$item_path/metadata.json")"
    if [[ -z "$message_id" ]]; then
        echo "Missing message_id for NAS item: $item" >&2
        exit 1
    fi
    printf '%s\t%s\t%s\n' "$item" "$revision" "$message_id"
done | sort -u >"$REMOTE_REVISIONS"

awk '{ print $3 "\t" $1 }' "$REMOTE_REVISIONS" >"$NAME_MAP"

echo "==> Migrating legacy laptop item names"

while read -r message_id new_name; do
    [[ "$new_name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]+$ ]] || continue
    [[ -d "$LOCAL_INBOX/$new_name" ]] && continue
    for legacy_path in "$LOCAL_INBOX"/*_"$message_id"; do
        legacy_name="$(basename "$legacy_path")"
        if [[ -d "$legacy_path" && "$legacy_name" =~ ^-?[0-9]+_[0-9]+$ ]]; then
            mv "$legacy_path" "$LOCAL_INBOX/$new_name"
            echo "Renamed laptop item: $legacy_name -> $new_name"
            break
        fi
    done
done <"$NAME_MAP"

while read -r item revision extra; do
    [[ -z "$item" ]] && continue
    if [[ ! "$item" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]+$ && ! "$item" =~ ^-?[0-9]+_[0-9]+$ ]]; then
        echo "Invalid delivered-items entry: $item" >&2
        exit 1
    fi
    if [[ -n "${extra:-}" ]]; then
        echo "Invalid delivered-items entry: $item ${revision:-} $extra" >&2
        exit 1
    fi
    if [[ -z "${revision:-}" ]]; then
        revision=0
    elif [[ ! "$revision" =~ ^[0-9]+$ ]]; then
        echo "Invalid delivered revision for $item: $revision" >&2
        exit 1
    fi
    if [[ "$item" =~ ^-?[0-9]+_([0-9]+)$ ]]; then
        new_name="$(awk -v message_id="${BASH_REMATCH[1]}" '$1 == message_id { print $2; exit }' "$NAME_MAP")"
        [[ -z "$new_name" ]] || item="$new_name"
    fi
    printf '%s\t%s\n' "$item" "$revision"
done <"$MANIFEST" | awk '
    !($1 in revisions) || $2 > revisions[$1] { revisions[$1] = $2 }
    END { for (item in revisions) print item "\t" revisions[item] }
' | sort -u >"$NORMALIZED_MANIFEST"
mv "$NORMALIZED_MANIFEST" "$MANIFEST"

echo "==> Removing items processed on the laptop"

while read -r item remote_revision message_id; do
    [[ -z "$item" ]] && continue
    delivered_revision="$(awk -v item="$item" '$1 == item { print $2; exit }' "$MANIFEST")"
    if [[ -n "$delivered_revision" && ! -d "$LOCAL_INBOX/$item" ]]; then
        if (( remote_revision <= delivered_revision )); then
            ssh "$REMOTE" "rm -rf -- '$REMOTE_INBOX/$item'"
            echo "Removed processed item: $item revision $remote_revision"
        else
            echo "Restoring updated item: $item revision $remote_revision"
        fi
    fi
done <"$REMOTE_REVISIONS"

echo "==> Downloading new and edited items"

rsync -azc \
    "${REMOTE}:${REMOTE_INBOX}/" \
    "${LOCAL_INBOX}/"

awk '
    NF >= 2 && (!($1 in revisions) || $2 > revisions[$1]) { revisions[$1] = $2 }
    END { for (item in revisions) print item "\t" revisions[item] }
' "$MANIFEST" "$REMOTE_REVISIONS" | sort -u >"$UPDATED_MANIFEST"
mv "$UPDATED_MANIFEST" "$MANIFEST"

echo "==> Synchronization complete"
echo "Laptop inbox: $LOCAL_INBOX"
