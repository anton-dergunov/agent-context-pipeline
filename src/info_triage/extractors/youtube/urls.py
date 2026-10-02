"""Parse and deduplicate individual YouTube video URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


@dataclass(frozen=True, slots=True)
class YouTubeReference:
    source_url: str
    video_id: str
    canonical_url: str
    explicit_short: bool


def parse_video_url(value: str) -> YouTubeReference:
    """Parse one supported video URL and reject collection/channel URLs."""
    source = value.strip()
    if not source:
        raise ValueError("empty YouTube URL")
    parsed = urlparse(source if "://" in source else f"https://{source}")
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)

    explicit_short = False
    video_id: str | None = None
    if host == "youtu.be":
        if len(parts) == 1:
            video_id = parts[0]
    elif host in YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            values = query.get("v", [])
            if len(values) == 1:
                video_id = values[0]
        elif len(parts) == 2 and parts[0] == "shorts":
            video_id = parts[1]
            explicit_short = True
    else:
        raise ValueError(f"not a YouTube URL: {value}")

    if "list" in query:
        raise ValueError(f"playlists are not supported: {value}")
    if not video_id or not VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError(f"not a supported individual YouTube video URL: {value}")
    return YouTubeReference(
        source_url=source,
        video_id=video_id,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        explicit_short=explicit_short,
    )


def load_inputs(values: list[str], input_file: Path | None) -> list[YouTubeReference]:
    raw = list(values)
    if input_file:
        raw.extend(
            line.strip()
            for line in input_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    if not raw:
        raise ValueError("provide one or more YouTube URLs, or use --input-file")

    references: list[YouTubeReference] = []
    seen: set[str] = set()
    for value in raw:
        reference = parse_video_url(value)
        if reference.video_id not in seen:
            references.append(reference)
            seen.add(reference.video_id)
    return references


def classify_video(
    reference: YouTubeReference,
    metadata: dict,
    override: str = "auto",
) -> tuple[str, str]:
    """Return ``(kind, basis)`` without treating every short-duration video as a Short."""
    if override in {"short", "video"}:
        return override, "explicit_override"
    if override != "auto":
        raise ValueError(f"unsupported YouTube kind: {override}")
    if reference.explicit_short:
        return "short", "shorts_url"

    duration = metadata.get("duration")
    aspect_ratio = metadata.get("aspect_ratio")
    if aspect_ratio is None:
        width = metadata.get("width")
        height = metadata.get("height")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and height:
            aspect_ratio = width / height
    if (
        isinstance(duration, (int, float))
        and duration <= 180
        and isinstance(aspect_ratio, (int, float))
        and aspect_ratio <= 1.0
    ):
        return "short", "portrait_duration_heuristic"
    return "video", "default_video"
