"""Fetch and parse Medium's documented profile and publication RSS feeds."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

import requests

from .urls import ARTICLE_ID_RE

CONTENT_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"
CREATOR_TAG = "{http://purl.org/dc/elements/1.1/}creator"
UPDATED_TAG = "{http://www.w3.org/2005/Atom}updated"
MAX_FEED_BYTES = 10 * 1024 * 1024
TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}
USER_AGENT = "info-triage-medium/0.1 (+personal low-volume RSS reader)"


@dataclass(slots=True)
class FeedError(RuntimeError):
    message: str
    kind: str = "failed"

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class FeedArticle:
    article_id: str
    title: str
    url: str
    author: str | None
    published_at: str | None
    updated_at: str | None
    categories: tuple[str, ...]
    html: str
    availability: str


def _text(element: ET.Element, tag: str) -> str | None:
    value = element.findtext(tag)
    return value.strip() if value and value.strip() else None


def _article_id(item: ET.Element) -> str | None:
    for tag in ("guid", "link"):
        value = _text(item, tag)
        if not value:
            continue
        candidate = value.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        match = ARTICLE_ID_RE.search(candidate)
        if match:
            return match.group(1).lower()
    return None


def parse_feed(xml: bytes) -> tuple[FeedArticle, ...]:
    """Parse a Medium RSS document without resolving any story-page URLs."""
    if len(xml) > MAX_FEED_BYTES:
        raise FeedError(f"RSS response exceeded {MAX_FEED_BYTES} bytes")
    upper_prefix = xml[:4096].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        raise FeedError("RSS response contains unsupported XML declarations")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise FeedError(f"invalid RSS XML: {exc}") from exc

    channel = root.find("channel")
    if root.tag != "rss" or channel is None or _text(channel, "generator") != "Medium":
        raise FeedError("response is not a Medium RSS feed")

    articles: list[FeedArticle] = []
    for item in channel.findall("item"):
        article_id = _article_id(item)
        title = _text(item, "title")
        url = _text(item, "link")
        full_html = _text(item, CONTENT_TAG)
        preview_html = _text(item, "description")
        if not article_id or not title or not url or not (full_html or preview_html):
            continue
        articles.append(
            FeedArticle(
                article_id=article_id,
                title=title,
                url=url.split("?", 1)[0],
                author=_text(item, CREATOR_TAG),
                published_at=_text(item, "pubDate"),
                updated_at=_text(item, UPDATED_TAG),
                categories=tuple(
                    value
                    for element in item.findall("category")
                    if (value := (element.text or "").strip())
                ),
                html=full_html or preview_html or "",
                availability="full" if full_html else "preview",
            )
        )
    return tuple(articles)


class MediumFeedClient:
    """Small anonymous client limited to Medium's published RSS interface."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retries: int = 2,
        requester: Callable[..., requests.Response] = requests.get,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self._requester = requester
        self._sleep = sleeper
        self._cache: dict[str, tuple[FeedArticle, ...]] = {}

    def get_articles(self, feed_url: str) -> tuple[FeedArticle, ...]:
        if feed_url in self._cache:
            return self._cache[feed_url]
        last_error: requests.RequestException | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self._requester(
                    feed_url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
                    },
                    cookies={},
                    allow_redirects=True,
                    timeout=(min(10.0, self.timeout), self.timeout),
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise FeedError(f"RSS request failed: {exc}") from exc
                self._sleep(float(2**attempt))
                continue

            if response.status_code in TRANSIENT_STATUSES and attempt < self.retries:
                response.close()
                self._sleep(float(2**attempt))
                continue
            try:
                try:
                    response.raise_for_status()
                except requests.RequestException as exc:
                    raise FeedError(f"Medium RSS returned HTTP {response.status_code}") from exc
                content_type = response.headers.get("Content-Type", "").lower()
                if not any(kind in content_type for kind in ("xml", "rss")):
                    raise FeedError(
                        f"expected RSS XML, received {content_type or 'an unknown content type'}"
                    )
                articles = parse_feed(response.content)
                self._cache[feed_url] = articles
                return articles
            finally:
                response.close()

        raise FeedError(f"RSS request failed: {last_error}")
