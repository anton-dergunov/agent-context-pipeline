"""Validate Medium article URLs and derive documented RSS feed URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

ARTICLE_ID_RE = re.compile(r"(?:^|-)([0-9a-f]{12})$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ArticleReference:
    source_url: str
    article_id: str
    feed_url: str


def parse_article_url(value: str) -> ArticleReference:
    """Return an article ID and its profile/publication RSS feed URL."""
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Medium article URLs must use HTTPS")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("Medium article URLs must not contain credentials or a custom port")

    path = parsed.path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    match = ARTICLE_ID_RE.search(slug)
    if match is None:
        raise ValueError("could not find a 12-character Medium article ID in the URL")

    host = parsed.hostname.lower().rstrip(".")
    segments = [segment for segment in path.split("/") if segment]
    if host in {"medium.com", "www.medium.com"}:
        if len(segments) < 2 or segments[0] == "p":
            raise ValueError("cannot derive a profile or publication feed from this Medium URL")
        feed_url = f"https://medium.com/feed/{segments[0]}"
    else:
        # Medium's documented custom-domain and username-subdomain feeds both
        # live at /feed. The response is later verified as a Medium RSS feed.
        feed_url = f"https://{host}/feed"

    query = parse_qs(parsed.query)
    # Medium Friend Links use `sk`; retain it because it deliberately grants
    # story access. Drop analytics and feed-source parameters.
    source_query = urlencode({"sk": query["sk"][0]}) if query.get("sk") else ""
    source_url = urlunsplit(("https", host, path, source_query, ""))
    return ArticleReference(source_url, match.group(1).lower(), feed_url)


def with_feed_url(reference: ArticleReference, value: str) -> ArticleReference:
    """Use a caller-supplied documented author/publication feed for a story."""
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Medium feed URLs must use HTTPS")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("Medium feed URLs must not contain credentials or a custom port")
    host = parsed.hostname.lower().rstrip(".")
    path = parsed.path.rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if host in {"medium.com", "www.medium.com"}:
        is_feed = len(segments) >= 2 and segments[0] == "feed"
    else:
        is_feed = segments == ["feed"]
    if not is_feed:
        raise ValueError("expected a Medium profile, publication, or custom-domain feed URL")
    feed_url = urlunsplit(("https", host, path, "", ""))
    return ArticleReference(reference.source_url, reference.article_id, feed_url)
