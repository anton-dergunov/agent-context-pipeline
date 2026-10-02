"""Parse and deduplicate supported LinkedIn post URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}
POST_ID_RE = re.compile(r"-(activity|share|ugcpost)-(\d+)(?:-|$)", re.IGNORECASE)
FEED_ID_RE = re.compile(r"^/feed/update/urn:li:(activity|share|ugcpost):(\d+)/?$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PostReference:
    source_url: str
    request_url: str
    post_id: str
    urn_type: str

    @property
    def target_urn(self) -> str:
        kind = "ugcPost" if self.urn_type == "ugcpost" else self.urn_type
        return f"urn:li:{kind}:{self.post_id}"


def parse_post_url(value: str) -> PostReference:
    source = value.strip()
    if not source:
        raise ValueError("empty LinkedIn URL")

    parsed = urlsplit(source if "://" in source else f"https://{source}")
    host = (parsed.hostname or "").lower()
    if host not in LINKEDIN_HOSTS:
        raise ValueError(f"not a LinkedIn URL: {source}")

    urn_type: str | None = None
    post_id: str | None = None
    if parsed.path.startswith("/posts/"):
        match = POST_ID_RE.search(parsed.path)
        if match:
            urn_type = match.group(1).lower()
            post_id = match.group(2)
    else:
        match = FEED_ID_RE.fullmatch(parsed.path)
        if match:
            urn_type = match.group(1).lower()
            post_id = match.group(2)

    if urn_type is None or post_id is None:
        raise ValueError(f"could not find a supported LinkedIn post ID in: {source}")

    clean_path = parsed.path if parsed.path.endswith("/") else parsed.path + "/"
    request_url = urlunsplit(("https", "www.linkedin.com", clean_path, "", ""))
    return PostReference(source, request_url, post_id, urn_type)


def load_inputs(values: list[str], input_file: Path | None) -> list[PostReference]:
    raw = list(values)
    if input_file:
        raw.extend(
            line.strip()
            for line in input_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    if not raw:
        raise ValueError("provide one or more LinkedIn post URLs, or use --input-file")

    result: list[PostReference] = []
    seen: set[str] = set()
    for value in raw:
        reference = parse_post_url(value)
        if reference.post_id not in seen:
            result.append(reference)
            seen.add(reference.post_id)
    return result
