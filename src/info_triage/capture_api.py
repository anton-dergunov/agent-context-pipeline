"""The transport-independent ingest contract behind `POST /capture`.

Telegram is one client of the system, not the system. Everything here builds the
same item a bot would, so nothing downstream has to know which transport a
capture arrived on.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import mimetypes
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import AttachmentSpec, CapturedItem, DownloadedAttachment
from .processing import ProcessingCoordinator
from .storage import HTTP_CHAT_ID, CaptureStore, now_iso

logger = logging.getLogger("info_triage")

# One attachment may be as large as a Telegram document, and the request that
# carries it has to be larger: base64 inflates by four thirds.
MAX_CAPTURE_FILE_BYTES = 20 * 1024 * 1024
MAX_CAPTURE_REQUEST_BYTES = 32 * 1024 * 1024
MAX_FILES = 20
# The handle a capture answers with: `<route>/<YYYY-MM-DD>_<local_id>`, which is
# also what `info-triage-capture` prints and how the sync manifest names an item.
ITEM_HANDLE = re.compile(r"^(?P<route>[a-z]+)/(?P<name>\d{4}-\d{2}-\d{2}_(?P<local_id>\d+))$")


class CaptureError(Exception):
    """A capture request that cannot be honoured, and the status that says so."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise CaptureError(f"{field} must be a string")
    return value


def _captured_at(value: Any) -> str:
    if value is None:
        return now_iso()
    try:
        moment = datetime.fromisoformat(_string(value, "captured_at"))
    except ValueError as error:
        raise CaptureError(f"captured_at is not an ISO-8601 timestamp: {error}") from error
    if moment.tzinfo is None:
        raise CaptureError("captured_at must include a timezone offset")
    return moment.astimezone(UTC).isoformat()


def _handle(value: Any, routes: tuple[str, ...]) -> tuple[str, str, int]:
    """Split a `<route>/<name>` handle into the parts needed to find its row."""
    match = ITEM_HANDLE.fullmatch(_string(value, "id"))
    if match is None:
        raise CaptureError("id must be an item handle of the form <route>/<YYYY-MM-DD>_<number>")
    if match["route"] not in routes:
        raise CaptureError(f"id names an unknown route; routes are: {', '.join(routes)}")
    return match["route"], match["name"], int(match["local_id"])


def _attachment(entry: Any, index: int, local_id: int) -> DownloadedAttachment:
    if not isinstance(entry, dict):
        raise CaptureError(f"files[{index}] must be an object")
    unknown = set(entry) - {"name", "mime_type", "data"}
    if unknown:
        raise CaptureError(f"files[{index}] has unknown field(s): {', '.join(sorted(unknown))}")
    name = _string(entry.get("name"), f"files[{index}].name")
    if not name.strip() or "/" in name or "\\" in name:
        raise CaptureError(f"files[{index}].name must be a bare file name")
    try:
        data = base64.b64decode(_string(entry.get("data"), f"files[{index}].data"), validate=True)
    except (binascii.Error, ValueError) as error:
        raise CaptureError(f"files[{index}].data is not valid base64: {error}") from error
    if not data:
        raise CaptureError(f"files[{index}].data is empty")
    if len(data) > MAX_CAPTURE_FILE_BYTES:
        raise CaptureError(
            f"files[{index}] is {len(data)} bytes, over the {MAX_CAPTURE_FILE_BYTES} byte limit",
            status=413,
        )
    mime_type = entry.get("mime_type")
    if mime_type is not None and not isinstance(mime_type, str):
        raise CaptureError(f"files[{index}].mime_type must be a string")
    spec = AttachmentSpec(
        kind="document",
        # Telegram concepts. Nothing re-fetches an attachment after capture, so a
        # content hash is a more honest filler than a fabricated id.
        file_id="",
        file_unique_id=hashlib.sha256(data).hexdigest()[:16],
        file_size=len(data),
        mime_type=mime_type or mimetypes.guess_type(name)[0],
        original_name=name,
        extension=Path(name).suffix.lower(),
        source_message_id=local_id,
    )
    return DownloadedAttachment(spec, data)


def parse_capture(body: bytes, routes: tuple[str, ...]) -> dict[str, Any]:
    """Validate one request body, without touching the store."""
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CaptureError(f"body is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise CaptureError("body must be a JSON object")
    unknown = set(value) - {"route", "source", "text", "captured_at", "files", "id"}
    if unknown:
        raise CaptureError(f"unknown field(s): {', '.join(sorted(unknown))}")

    handle = None if value.get("id") is None else _handle(value["id"], routes)
    route = _string(value.get("route"), "route")
    if route not in routes:
        raise CaptureError(f"route must be one of: {', '.join(routes)}")
    if handle is not None and value.get("captured_at") is not None:
        # The handle encodes the capture date, so a new one would rename the
        # directory out from under the caller that just used it.
        raise CaptureError("captured_at cannot be changed; it is part of the item's id")
    text = _string(value.get("text", ""), "text").strip()
    files = value.get("files") or []
    if not isinstance(files, list):
        raise CaptureError("files must be a list")
    if len(files) > MAX_FILES:
        raise CaptureError(f"files must hold at most {MAX_FILES} entries", status=413)
    if not text and not files:
        raise CaptureError("text is required unless files are attached")
    source = value.get("source")
    if source is not None and not isinstance(source, str):
        raise CaptureError("source must be a string")
    return {
        "route": route,
        "source": (source or "http").strip() or "http",
        "text": text,
        "captured_at": None if handle is not None else _captured_at(value.get("captured_at")),
        "files": files,
        "handle": handle,
    }


def _payload(request: dict[str, Any], message_id: int, captured_at: str) -> dict[str, Any]:
    """Build the capture in Telegram's own payload shape.

    Link discovery, segment rendering and index rendering then read it without a
    second code path. Empty entities means discovery falls back to the visible
    text, which is right here.
    """
    return {
        "message_id": message_id,
        "date": int(datetime.fromisoformat(captured_at).timestamp()),
        "text": request["text"],
        "entities": [],
        "source": request["source"],
    }


def perform_capture(
    store: CaptureStore,
    coordinator: ProcessingCoordinator,
    request: dict[str, Any],
) -> CapturedItem:
    """Commit a validated request as an item and hand it to the pipeline.

    One request is one item: the Telegram grouping window exists because Telegram
    splits one thought across several messages, and an HTTP client can send one
    body.
    """
    if request["handle"] is not None:
        return perform_replacement(store, request)
    route = request["route"]
    captured_at = request["captured_at"]
    with store.lock:
        local_id = store.allocate_local_id(route)
        attachments = [
            _attachment(entry, index, local_id) for index, entry in enumerate(request["files"])
        ]
        return store.capture(
            route,
            HTTP_CHAT_ID,
            local_id,
            request["text"],
            route=route,
            received_at=captured_at,
            capture_payload=_payload(request, local_id, captured_at),
            attachments=attachments,
            replace_attachment_source_ids=set(),
            source_message_ids=[local_id],
            reserved_local_id=local_id,
        )


def perform_replacement(store: CaptureStore, request: dict[str, Any]) -> CapturedItem:
    """Rewrite the item a handle names, at a bumped revision.

    A replacement, not a patch: the request body is the item's whole new content,
    so the files it omits are gone and everything the last revision generated is
    discarded. What it cannot change is when the capture happened — that is in
    the handle — or the item's identity, which stays on the route that first
    received it so a later request by the new handle still resolves.
    """
    handle_route, name, local_id = request["handle"]
    with store.lock:
        existing = store.item_by_local_id(handle_route, local_id)
        if existing is None or store.item_name(existing["created_at"], local_id) != name:
            raise CaptureError(f"no item named {handle_route}/{name}", status=404)
        if existing["chat_id"] != HTTP_CHAT_ID:
            raise CaptureError(
                f"{handle_route}/{name} was captured from Telegram; edit the message instead",
                status=409,
            )
        message_id = existing["message_id"]
        created_at = existing["created_at"]
        item = store.capture(
            existing["origin_route"],
            HTTP_CHAT_ID,
            message_id,
            request["text"],
            route=request["route"],
            capture_payload=_payload(request, message_id, created_at),
            attachments=[
                _attachment(entry, index, message_id)
                for index, entry in enumerate(request["files"])
            ],
            # None, not an empty set: keep nothing from the previous manifest.
            replace_attachment_source_ids=None,
            source_message_ids=[message_id],
            force_revision=True,
        )
        try:
            store.reset_generated_output(item.path)
        except OSError as error:
            # The item is already committed, and the pipeline overwrites what it
            # regenerates. A stale file is a blemish; never submitting is a loss.
            logger.warning("Could not clear generated output of %s: %s", item.path.name, error)
    return item


def capture(
    store: CaptureStore,
    coordinator: ProcessingCoordinator,
    body: bytes,
    routes: tuple[str, ...],
) -> tuple[int, dict[str, Any]]:
    """Parse, commit and submit one capture; return the status and response body."""
    request = parse_capture(body, routes)
    item = perform_capture(store, coordinator, request)
    coordinator.submit(item)
    # The route and id are the new ones, so a caller that moved an item between
    # routes learns the handle it now has to use.
    return 200 if request["handle"] is not None else 201, {
        "route": item.route,
        "id": item.path.name,
        "revision": item.revision,
        "status": item.status,
    }
