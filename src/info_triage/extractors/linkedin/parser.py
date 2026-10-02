"""Parse public LinkedIn post HTML into reusable data models."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from .urls import PostReference

URL_RE = re.compile(r"https?://[^\s<>\"\]]+", re.IGNORECASE)
INTEGER_RE = re.compile(r"\d[\d,]*")


class ParseError(ValueError):
    pass


@dataclass(slots=True)
class Link:
    text: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class Image:
    url: str
    alt_text: str = ""
    kind: str = "post_image"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class Comment:
    index: int
    text: str
    author_name: str | None = None
    author_url: str | None = None
    published_at: str | None = None
    relative_time: str | None = None
    likes_count: int | None = None
    parent_index: int | None = None
    links: list[Link] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["links"] = [item.to_dict() for item in self.links]
        return value


@dataclass(slots=True)
class ParsedPost:
    post_id: str
    source_url: str
    request_url: str
    final_url: str
    activity_urn: str | None
    attributed_urn: str | None
    featured_activity_urn: str | None
    author_name: str | None
    author_url: str | None
    resharer_name: str | None
    resharer_url: str | None
    reshare_context: str | None
    published_at: str | None
    relative_time: str | None
    text: str
    headline: str | None
    content_type: str
    reaction_count: int | None
    reported_comment_count: int
    links: list[Link]
    images: list[Image]
    comments: list[Comment]
    raw_json_ld: dict[str, Any] | None
    extraction_sources: dict[str, str]


def _normalized_text(tag: Tag | None) -> str:
    if tag is None:
        return ""
    text = tag.get_text("", strip=False).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _clean_linkedin_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme:
        return url
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)
    if host in {"linkedin.com", "www.linkedin.com"} and parsed.path in {
        "/redir/redirect",
        "/safety/go",
    }:
        values = query.get("url", ())
        if values and urlsplit(values[0]).scheme in {"http", "https"}:
            return values[0]
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        pairs = [(key, value) for key, values in query.items() if key != "trk" for value in values]
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), parsed.fragment)
        )
    return url


def _trim_url_punctuation(value: str) -> str:
    while value and value[-1] in ".,;:!?'":
        value = value[:-1]
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    return value


def _links(tag: Tag | None, text: str = "") -> list[Link]:
    result: list[Link] = []
    seen: set[str] = set()
    if tag is not None:
        for anchor in tag.select("a[href]"):
            label = anchor.get_text(" ", strip=True)
            if label.startswith(("http://", "https://")):
                url = _trim_url_punctuation(label)
            else:
                url = _clean_linkedin_url(str(anchor.get("href", "")))
            if url.startswith(("http://", "https://")) and url not in seen:
                result.append(Link(label, url))
                seen.add(url)
    for match in URL_RE.finditer(text):
        url = _trim_url_punctuation(match.group(0))
        if url and url not in seen:
            result.append(Link(url, url))
            seen.add(url)
    return result


def _integer(tag: Tag | None) -> int | None:
    if tag is None:
        return None
    match = INTEGER_RE.search(tag.get_text(" ", strip=True))
    return int(match.group(0).replace(",", "")) if match else None


def _json_ld(soup: BeautifulSoup, activity_urn: str | None) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        values = value if isinstance(value, list) else [value]
        candidates.extend(
            item
            for item in values
            if isinstance(item, dict) and item.get("@type") == "SocialMediaPosting"
        )
    if activity_urn:
        activity_id = activity_urn.rsplit(":", 1)[-1]
        for item in candidates:
            if activity_id in str(item.get("@id", "")):
                return item
    return candidates[0] if len(candidates) == 1 else None


def _find_card(soup: BeautifulSoup, reference: PostReference) -> Tag:
    cards = soup.select("article.main-feed-activity-card")
    attribute = "data-activity-urn" if reference.urn_type == "activity" else "data-attributed-urn"
    for card in cards:
        if card.get(attribute) == reference.target_urn:
            return card
    # Some public pages expose a direct share as the activity rather than the
    # attributed URN. This fallback remains ID-exact and cannot select a related post.
    for card in cards:
        urns = {
            card.get("data-activity-urn"),
            card.get("data-attributed-urn"),
            card.get("data-featured-activity-urn"),
        }
        if any(value and value.rsplit(":", 1)[-1] == reference.post_id for value in urns):
            return card
    raise ParseError(f"could not find the requested post card for {reference.target_urn}")


def _profile(card: Tag) -> tuple[str | None, str | None]:
    lockup = card.select_one('[data-test-id="main-feed-activity-card__entity-lockup"]')
    anchor = lockup.select_one('a[data-tracking-control-name$="actor-name"]') if lockup else None
    if anchor is None and lockup:
        anchor = lockup.select_one("a[href]")
    if anchor is None:
        return None, None
    return anchor.get_text(" ", strip=True) or None, _clean_linkedin_url(
        str(anchor.get("href", ""))
    ) or None


def _resharer(card: Tag) -> tuple[str | None, str | None, str | None]:
    header = card.select_one(".main-feed-activity-card__header")
    if header is None:
        return None, None, None
    anchor = header.select_one("a[href]")
    return (
        anchor.get_text(" ", strip=True) or None if anchor else None,
        _clean_linkedin_url(str(anchor.get("href", ""))) or None if anchor else None,
        header.get_text(" ", strip=True) or None,
    )


def _images(card: Tag, json_ld: dict[str, Any] | None) -> list[Image]:
    result: list[Image] = []
    seen: set[str] = set()
    selectors = (
        '[data-test-id="feed-images-content"] img[data-delayed-url]',
        ".feed-shared-article img[data-delayed-url]",
        '[data-test-id*="article"] img[data-delayed-url]',
    )
    for selector in selectors:
        for image in card.select(selector):
            url = str(image.get("data-delayed-url", ""))
            if url.startswith(("http://", "https://")) and url not in seen:
                kind = "article_thumbnail" if "article" in selector else "post_image"
                result.append(Image(url=url, alt_text=str(image.get("alt", "")), kind=kind))
                seen.add(url)

    if not result and json_ld:
        raw_images = json_ld.get("image") or []
        if not isinstance(raw_images, list):
            raw_images = [raw_images]
        for item in raw_images:
            url = item.get("url") if isinstance(item, dict) else item
            if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
                result.append(Image(url=url))
                seen.add(url)
    return result


def _json_comment_likes(value: dict[str, Any]) -> int | None:
    statistics = value.get("interactionStatistic") or []
    if not isinstance(statistics, list):
        statistics = [statistics]
    for item in statistics:
        if isinstance(item, dict) and "Like" in str(item.get("interactionType", "")):
            try:
                return int(item.get("userInteractionCount"))
            except (TypeError, ValueError):
                return None
    return None


def _comments(card: Tag, json_ld: dict[str, Any] | None, maximum: int) -> list[Comment]:
    result: list[Comment] = []
    sections = card.select("section.comment")[:maximum]
    section_indexes = {id(section): index for index, section in enumerate(sections, 1)}
    for index, section in enumerate(sections, 1):
        text_tag = section.select_one("p.comment__text")
        text = _normalized_text(text_tag)
        if not text:
            continue
        author = section.select_one("a.comment__author")
        relative = section.select_one(".comment__duration-since")
        reactions = section.select_one('a[href*="comment_reactions"]')
        parent = section.find_parent("section", class_="comment")
        result.append(
            Comment(
                index=index,
                text=text,
                author_name=author.get_text(" ", strip=True) or None if author else None,
                author_url=_clean_linkedin_url(str(author.get("href", ""))) or None
                if author
                else None,
                relative_time=relative.get_text(" ", strip=True) or None if relative else None,
                likes_count=_integer(reactions),
                parent_index=section_indexes.get(id(parent)) if parent else None,
                links=_links(text_tag, text),
            )
        )

    raw_comments = (json_ld or {}).get("comment") or []
    if not isinstance(raw_comments, list):
        raw_comments = [raw_comments]
    if not result:
        for index, value in enumerate(raw_comments[:maximum], 1):
            if not isinstance(value, dict) or not value.get("text"):
                continue
            author = value.get("author") or {}
            result.append(
                Comment(
                    index=index,
                    text=str(value["text"]),
                    author_name=author.get("name") if isinstance(author, dict) else None,
                    published_at=value.get("datePublished"),
                    likes_count=_json_comment_likes(value),
                    links=_links(None, str(value["text"])),
                )
            )
    else:
        for comment, value in zip(result, raw_comments):
            if (
                not isinstance(value, dict)
                or str(value.get("text", "")).strip() != comment.text.strip()
            ):
                continue
            comment.published_at = value.get("datePublished")
            if comment.likes_count is None:
                comment.likes_count = _json_comment_likes(value)
    return result


def parse_post(
    html: str, reference: PostReference, *, final_url: str, max_comments: int = 50
) -> ParsedPost:
    if max_comments < 0:
        raise ValueError("max_comments must be non-negative")
    soup = BeautifulSoup(html, "html.parser")
    card = _find_card(soup, reference)
    activity_urn = card.get("data-activity-urn")
    json_ld = _json_ld(soup, str(activity_urn) if activity_urn else None)

    body_tag = card.select_one('[data-test-id="main-feed-activity-card__commentary"]')
    text = _normalized_text(body_tag)
    text_source = "semantic_html"
    if not text and json_ld:
        text = str(
            json_ld.get("articleBody") or json_ld.get("text") or json_ld.get("headline") or ""
        ).strip()
        text_source = "json_ld"
    if not text:
        raise ParseError("the requested post card did not contain post text")

    author_name, author_url = _profile(card)
    resharer_name, resharer_url, reshare_context = _resharer(card)
    images = _images(card, json_ld)
    comments = _comments(card, json_ld, max_comments)
    count_tag = card.select_one('[data-test-id="social-actions__comments"]')
    try:
        reported_comments = int(count_tag.get("data-num-comments", 0)) if count_tag else 0
    except (TypeError, ValueError):
        reported_comments = int((json_ld or {}).get("commentCount") or len(comments))

    links = _links(body_tag, text)
    published_at = (json_ld or {}).get("datePublished")
    time_tag = card.select_one("time")
    relative_time = time_tag.get_text(" ", strip=True) if time_tag else None
    reaction_count = _integer(card.select_one('[data-test-id="social-actions__reaction-count"]'))
    if card.select_one("video, [data-test-id*='video']"):
        content_type = "video"
    elif card.select_one("iframe[src*='native-document'], [data-test-id*='document']"):
        content_type = "document"
    elif len(images) > 1:
        content_type = "multi_image"
    elif images:
        content_type = "image"
    else:
        content_type = "text"

    return ParsedPost(
        post_id=reference.post_id,
        source_url=reference.source_url,
        request_url=reference.request_url,
        final_url=final_url,
        activity_urn=str(activity_urn) if activity_urn else None,
        attributed_urn=card.get("data-attributed-urn"),
        featured_activity_urn=card.get("data-featured-activity-urn"),
        author_name=author_name or (json_ld or {}).get("author", {}).get("name"),
        author_url=author_url,
        resharer_name=resharer_name,
        resharer_url=resharer_url,
        reshare_context=reshare_context,
        published_at=str(published_at) if published_at else None,
        relative_time=relative_time,
        text=text,
        headline=(json_ld or {}).get("headline"),
        content_type=content_type,
        reaction_count=reaction_count,
        reported_comment_count=reported_comments,
        links=links,
        images=images,
        comments=comments,
        raw_json_ld=json_ld,
        extraction_sources={
            "post_text": text_source,
            "metadata": "json_ld+semantic_html" if json_ld else "semantic_html",
            "comments": "anonymous_public_selection",
        },
    )
