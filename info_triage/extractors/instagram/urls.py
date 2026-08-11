"""Parse and deduplicate supported Instagram URLs."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SUPPORTED_PATH_TYPES = {"p", "reel", "reels", "tv"}


def shortcode_from_url(value: str) -> str:
    """Return an Instagram shortcode from a URL or accept a bare shortcode."""
    value = value.strip()
    if not value:
        raise ValueError("empty Instagram URL")
    if SHORTCODE_RE.fullmatch(value) and "/" not in value:
        return value

    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower()
    if host not in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        raise ValueError(f"not an Instagram URL: {value}")
    parts = [part for part in parsed.path.split("/") if part]
    for index, part in enumerate(parts[:-1]):
        if part in SUPPORTED_PATH_TYPES and SHORTCODE_RE.fullmatch(parts[index + 1]):
            return parts[index + 1]
    raise ValueError(f"could not find a post shortcode in: {value}")


def load_inputs(values: list[str], input_file: Path | None) -> list[tuple[str, str]]:
    raw = list(values)
    if input_file:
        raw.extend(
            line.strip()
            for line in input_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    if not raw:
        raise ValueError("provide one or more URLs, or use --input-file")

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value in raw:
        shortcode = shortcode_from_url(value)
        if shortcode not in seen:
            result.append((value, shortcode))
            seen.add(shortcode)
    return result
