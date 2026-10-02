"""URL matching and dispatch metadata for standalone extractors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from info_triage.extractors.document.extractor import item_id, normalized_url
from info_triage.extractors.instagram.urls import shortcode_from_url
from info_triage.extractors.linkedin.urls import parse_post_url
from info_triage.extractors.medium.urls import parse_article_url
from info_triage.extractors.research.providers import match_research_url
from info_triage.extractors.youtube.urls import parse_video_url
from info_triage.utilities.url_resolution import URLResolver, is_redirector_url


@dataclass(frozen=True, slots=True)
class GenericReference:
    source_url: str
    canonical_url: str
    document_id: str


@dataclass(frozen=True, slots=True)
class InstagramReference:
    source_url: str
    shortcode: str


@dataclass(frozen=True, slots=True)
class RouteResult:
    handler: str
    identity: str
    source_url: str
    routed_url: str
    reference: Any


def _try(parser: Callable[[str], Any], value: str) -> Any | None:
    try:
        return parser(value)
    except ValueError:
        return None


def _match_specialized(value: str) -> RouteResult | None:
    if shortcode := _try(shortcode_from_url, value):
        reference = InstagramReference(value, shortcode)
        return RouteResult("instagram", shortcode, value, value, reference)
    if reference := _try(parse_post_url, value):
        return RouteResult("linkedin", reference.post_id, value, value, reference)
    if reference := _try(parse_video_url, value):
        return RouteResult("youtube", reference.video_id, value, value, reference)
    if reference := match_research_url(value):
        return RouteResult(
            "research",
            f"{reference.provider}:{reference.paper_id}",
            value,
            value,
            reference,
        )
    if reference := _try(parse_article_url, value):
        return RouteResult("medium", reference.article_id, value, value, reference)
    return None


def route_url(
    value: str,
    *,
    redirect_resolver: Callable[[str], str] | None = None,
    resolve_redirectors: bool = True,
) -> RouteResult:
    """Select the first matching extractor and return its canonical identity.

    Recognized shorteners are resolved only after direct specialized matching,
    then the resulting URL receives one fresh specialized routing pass.
    """
    source = value.strip()
    if not source:
        raise ValueError("empty URL")
    if result := _match_specialized(source):
        return result

    routed = source
    if resolve_redirectors and is_redirector_url(source):
        if redirect_resolver is None:
            resolver = URLResolver(
                timeout=30.0,
                retries=2,
                max_html_bytes=10 * 1024 * 1024,
                max_pdf_bytes=50 * 1024 * 1024,
            )
            routed = resolver.resolve(source)
        else:
            routed = redirect_resolver(source)
        if routed != source and (result := _match_specialized(routed)):
            return RouteResult(
                result.handler,
                result.identity,
                source,
                routed,
                result.reference,
            )

    parsed = urlsplit(routed)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("generic extraction requires an HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("credential-bearing URLs are not supported")
    canonical = normalized_url(routed)
    reference = GenericReference(source, canonical, item_id(canonical))
    return RouteResult("document", reference.document_id, source, routed, reference)
