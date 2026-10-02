"""Render retained Telegram payloads as stable Markdown segments."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from telegram import Message

from .utilities.markdown import escape_markdown_destination, escape_markdown_label

SEGMENT_HEADING_RE = re.compile(r"^## Segment [1-9][0-9]* — [a-z-]+(?: [a-z-]+)*$")

ATTACHMENT_KINDS = (
    "document",
    "photo",
    "video",
    "animation",
    "audio",
    "voice",
    "video_note",
)


def location_markdown(message: Message) -> str:
    location = message.location or (message.venue.location if message.venue else None)
    if location is None:
        return ""
    if message.venue:
        heading = message.venue.title
        details = [message.venue.address]
    else:
        heading = "Location"
        details = []
    coordinates = f"{location.latitude},{location.longitude}"
    details.extend(
        [
            f"Coordinates: {coordinates}",
            f"[Open in maps](https://www.google.com/maps/search/?api=1&query={coordinates})",
        ]
    )
    return "\n".join([f"### {heading}", *details])


def message_content(message: Message) -> str | None:
    """Return useful Markdown, or None for deliberately ignored messages.

    This is the capture-time ignore filter only. The delivered body is always
    rendered from the retained payloads by `render_capture_payloads`, so hidden
    hyperlink destinations are restored there rather than duplicated here.
    """
    text = message.text if message.text is not None else message.caption
    location = location_markdown(message)
    if text is not None or location or any(getattr(message, kind) for kind in ATTACHMENT_KINDS):
        return "\n\n".join(part for part in (text or "", location) if part)
    return None


def entity_slice(text: str, offset: Any, length: Any) -> str | None:
    """Return the text one entity covers, honouring Telegram's UTF-16 offsets.

    Telegram counts offsets in UTF-16 code units, so a single emoji earlier in
    the message shifts every later entity by two.
    """
    if isinstance(offset, bool) or isinstance(length, bool):
        return None
    if not isinstance(offset, int) or not isinstance(length, int):
        return None
    if offset < 0 or length <= 0:
        return None
    units = text.encode("utf-16-le")
    start = offset * 2
    end = start + length * 2
    if end > len(units):
        return None
    try:
        return units[start:end].decode("utf-16-le")
    except UnicodeDecodeError:
        # The span cuts a surrogate pair in half and cannot describe real text.
        return None


def _text_link_spans(text: str, entities: Any) -> list[tuple[int, int, str, str]]:
    spans = []
    for entity in entities if isinstance(entities, list) else ():
        if not isinstance(entity, dict) or entity.get("type") != "text_link":
            continue
        url = entity.get("url")
        if not isinstance(url, str) or urlsplit(url).scheme.lower() not in ("http", "https"):
            continue
        offset = entity.get("offset")
        label = entity_slice(text, offset, entity.get("length"))
        # A label spanning a line break cannot become one Markdown link. Leave the
        # text alone; link discovery still reads the destination from the entity.
        if label is None or not label.strip() or "\n" in label:
            continue
        spans.append((offset, len(label.encode("utf-16-le")) // 2, label, url))
    return sorted(spans)


def apply_text_link_entities(text: str, entities: Any) -> str:
    """Restore the hyperlink destinations Telegram drops from plain message text."""
    spans = _text_link_spans(text, entities)
    if not spans:
        return text
    units = text.encode("utf-16-le")
    pieces = []
    cursor = 0
    for offset, length, label, url in spans:
        if offset < cursor:
            continue  # Overlaps an entity that was already applied.
        pieces.append(units[cursor * 2 : offset * 2].decode("utf-16-le"))
        pieces.append(f"[{escape_markdown_label(label)}]({escape_markdown_destination(url)})")
        cursor = offset + length
    pieces.append(units[cursor * 2 :].decode("utf-16-le"))
    return "".join(pieces)


def _payload_location_markdown(payload: dict[str, Any]) -> str:
    venue = payload.get("venue")
    location = venue.get("location") if isinstance(venue, dict) else payload.get("location")
    if not isinstance(location, dict):
        return ""
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if latitude is None or longitude is None:
        return ""
    if isinstance(venue, dict):
        heading = str(venue.get("title") or "Venue")
        details = [str(venue["address"])] if venue.get("address") else []
    else:
        heading = "Location"
        details = []
    coordinates = f"{latitude},{longitude}"
    details.extend(
        [
            f"Coordinates: {coordinates}",
            f"[Open in maps](https://www.google.com/maps/search/?api=1&query={coordinates})",
        ]
    )
    return "\n".join([f"### {heading}", *details])


def payload_content(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    entities = payload.get("entities")
    if text is None:
        text = payload.get("caption")
        entities = payload.get("caption_entities")
    body = apply_text_link_entities(str(text or ""), entities)
    location = _payload_location_markdown(payload)
    return "\n\n".join(value for value in (body, location) if value)


def payload_order(payload: dict[str, Any]) -> tuple[int, int]:
    return int(payload.get("date", 0)), int(payload["message_id"])


def is_forwarded(payload: dict[str, Any]) -> bool:
    return any(
        payload.get(key) is not None
        for key in (
            "forward_origin",
            "forward_from",
            "forward_from_chat",
            "forward_sender_name",
        )
    )


def segment_kind(payload: dict[str, Any]) -> str:
    if payload.get("voice") is not None:
        kind = "voice"
    elif payload.get("text") is not None:
        kind = "text"
    elif payload.get("caption") is not None:
        kind = "caption"
    elif payload.get("venue") is not None:
        kind = "venue"
    elif payload.get("location") is not None:
        kind = "location"
    else:
        kind = next(
            (name.replace("_", "-") for name in ATTACHMENT_KINDS if payload.get(name)),
            "message",
        )
    return f"forwarded {kind}" if is_forwarded(payload) else kind


def render_capture_payloads(
    payloads: list[dict[str, Any]],
    *,
    voice_transcripts: Mapping[int, Sequence[str]] | None = None,
) -> str:
    """Render every retained Telegram message as one ordered Markdown segment."""
    sections = []
    for index, payload in enumerate(sorted(payloads, key=payload_order), 1):
        content = payload_content(payload)
        transcripts = (voice_transcripts or {}).get(payload["message_id"], ())
        transcript_text = "\n\n".join(
            str(value).strip() for value in transcripts if str(value).strip()
        )
        body = "\n\n".join(value for value in (transcript_text, content) if value)
        heading = f"## Segment {index} — {segment_kind(payload)}"
        sections.append(f"{heading}\n\n{body}" if body else heading)
    return "\n\n".join(sections)
