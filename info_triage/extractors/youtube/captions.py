"""Select and normalize original-language YouTube caption tracks."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MIN_VISIBLE_CHARACTERS = 20
MIN_CUE_SPAN_RATIO = 0.25


@dataclass(frozen=True, slots=True)
class CaptionTrack:
    language: str
    source: str


@dataclass(frozen=True, slots=True)
class CaptionSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class CaptionResult:
    status: str
    source: str | None
    language: str | None
    text: str
    segments: tuple[CaptionSegment, ...] = ()
    visible_characters: int = 0
    cue_span_ratio: float = 0.0
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "segments": [asdict(segment) for segment in self.segments],
        }


def _language_base(value: str | None) -> str | None:
    if not value:
        return None
    return value.casefold().split("-", 1)[0]


def _has_json3(formats: Any) -> bool:
    return isinstance(formats, list) and any(
        isinstance(item, dict) and item.get("ext") == "json3" for item in formats
    )


def _matching_language(keys: list[str], language: str | None) -> str | None:
    if not language:
        return keys[0] if len(keys) == 1 else None
    folded = language.casefold()
    exact = next((key for key in keys if key.casefold() == folded), None)
    if exact:
        return exact
    base = _language_base(language)
    return next((key for key in keys if _language_base(key) == base), None)


def select_caption_track(metadata: dict[str, Any]) -> CaptionTrack | None:
    """Prefer matching manual captions, then a genuine ``*-orig`` automatic track."""
    language = metadata.get("language") or metadata.get("original_language")
    subtitles = metadata.get("subtitles") or {}
    manual_keys = [
        key for key, formats in subtitles.items() if key != "live_chat" and _has_json3(formats)
    ]
    if selected := _matching_language(manual_keys, language):
        return CaptionTrack(selected, "youtube_manual")

    automatic = metadata.get("automatic_captions") or {}
    original_keys = [
        key for key, formats in automatic.items() if key.endswith("-orig") and _has_json3(formats)
    ]
    language_base = _language_base(language)
    selected = next(
        (
            key
            for key in original_keys
            if _language_base(key.removesuffix("-orig")) == language_base
        ),
        None,
    )
    if selected is None and len(original_keys) == 1:
        selected = original_keys[0]
    return CaptionTrack(selected, "youtube_automatic") if selected else None


def _event_text(event: dict[str, Any]) -> str:
    pieces = [
        str(segment.get("utf8", ""))
        for segment in event.get("segs", [])
        if isinstance(segment, dict)
    ]
    return re.sub(r"\s+", " ", "".join(pieces)).strip()


def parse_json3(
    path: Path,
    track: CaptionTrack,
    duration_seconds: float | int | None,
) -> CaptionResult:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return CaptionResult(
            status="rejected",
            source=track.source,
            language=track.language,
            text="",
            reason=f"invalid_caption_json: {type(error).__name__}: {error}",
        )

    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return CaptionResult(
            status="rejected",
            source=track.source,
            language=track.language,
            text="",
            reason="invalid_caption_structure",
        )

    segments: list[CaptionSegment] = []
    for event in payload["events"]:
        if not isinstance(event, dict):
            continue
        text = _event_text(event)
        if not text:
            continue
        start = float(event.get("tStartMs", 0)) / 1000
        end = start + float(event.get("dDurationMs", 0)) / 1000
        segments.append(CaptionSegment(start=start, end=end, text=text))

    text = " ".join(segment.text for segment in segments).strip()
    visible_characters = len(re.sub(r"\s+", "", text))
    duration = float(duration_seconds or 0)
    cue_span = (
        (max(item.end for item in segments) - min(item.start for item in segments))
        if segments
        else 0
    )
    cue_span_ratio = min(1.0, cue_span / duration) if duration > 0 else 0.0
    reason: str | None = None
    if visible_characters < MIN_VISIBLE_CHARACTERS:
        reason = "caption_too_short"
    elif cue_span_ratio < MIN_CUE_SPAN_RATIO:
        reason = "caption_span_too_short"
    return CaptionResult(
        status="complete" if reason is None else "rejected",
        source=track.source,
        language=track.language,
        text=text if reason is None else "",
        segments=tuple(segments) if reason is None else (),
        visible_characters=visible_characters,
        cue_span_ratio=cue_span_ratio,
        reason=reason,
    )


def unavailable(reason: str) -> CaptionResult:
    return CaptionResult(
        status="unavailable",
        source=None,
        language=None,
        text="",
        reason=reason,
    )
