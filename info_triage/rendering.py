"""Render retained Telegram payloads as stable Markdown segments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from telegram import Message

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
    """Return useful Markdown, or None for deliberately ignored messages."""
    text = message.text if message.text is not None else message.caption
    location = location_markdown(message)
    if text is not None or location or any(getattr(message, kind) for kind in ATTACHMENT_KINDS):
        return "\n\n".join(part for part in (text or "", location) if part)
    return None


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
    if text is None:
        text = payload.get("caption")
    location = _payload_location_markdown(payload)
    return "\n\n".join(value for value in (str(text or ""), location) if value)


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
