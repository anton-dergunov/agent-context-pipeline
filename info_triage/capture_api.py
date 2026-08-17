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
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import AttachmentSpec, CapturedItem, DownloadedAttachment
from .processing import ProcessingCoordinator
from .storage import HTTP_CHAT_ID, CaptureStore, now_iso

# One attachment may be as large as a Telegram document, and the request that
# carries it has to be larger: base64 inflates by four thirds.
MAX_CAPTURE_FILE_BYTES = 20 * 1024 * 1024
MAX_CAPTURE_REQUEST_BYTES = 32 * 1024 * 1024
MAX_FILES = 20


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
    unknown = set(value) - {"route", "source", "text", "captured_at", "files"}
    if unknown:
        raise CaptureError(f"unknown field(s): {', '.join(sorted(unknown))}")

    route = _string(value.get("route"), "route")
    if route not in routes:
        raise CaptureError(f"route must be one of: {', '.join(routes)}")
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
        "captured_at": _captured_at(value.get("captured_at")),
        "files": files,
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
    route = request["route"]
    captured_at = request["captured_at"]
    with store.lock:
        local_id = store.allocate_local_id(route)
        attachments = [
            _attachment(entry, index, local_id) for index, entry in enumerate(request["files"])
        ]
        # Telegram's own payload shape, so link discovery, segment rendering and
        # index rendering all read it without a second code path. Empty entities
        # means discovery falls back to the visible text, which is right here.
        payload = {
            "message_id": local_id,
            "date": int(datetime.fromisoformat(captured_at).timestamp()),
            "text": request["text"],
            "entities": [],
            "source": request["source"],
        }
        return store.capture(
            route,
            HTTP_CHAT_ID,
            local_id,
            request["text"],
            route=route,
            received_at=captured_at,
            capture_payload=payload,
            attachments=attachments,
            replace_attachment_source_ids=set(),
            source_message_ids=[local_id],
            reserved_local_id=local_id,
        )


def capture(
    store: CaptureStore,
    coordinator: ProcessingCoordinator,
    body: bytes,
    routes: tuple[str, ...],
) -> dict[str, Any]:
    """Parse, commit and submit one capture; return the response body."""
    item = perform_capture(store, coordinator, parse_capture(body, routes))
    coordinator.submit(item)
    return {
        "route": item.route,
        "id": item.path.name,
        "revision": item.revision,
        "status": item.status,
    }
