"""Discover, canonicalize and rank every link a captured item carries.

Discovery is deliberately offline. Redirector destinations that only a request
can reveal are resolved by the URL resolution step, which owns the single
network pass and updates the table in place.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .extractors.artifacts import HarvestedLink
from .extractors.router import route_url
from .models import LinkTableEntry
from .rendering import SEGMENT_HEADING_RE, entity_slice, payload_order
from .utilities.url_resolution import embedded_destination, iter_link_occurrences

# Parameters that identify the sender rather than the destination. This list is
# separate from text cleaning's UNWANTED_PARAMS because destinations resolved
# after cleaning never pass through the cleaner again.
TRACKING_PARAMETERS = frozenset({"cbrd", "igsh", "si", "is", "fbclid", "rcm"})
TRACKING_PARAMETER_PREFIXES = ("utm_", "cp_landing")

DEFAULT_PORTS = {"http": "80", "https": "443"}

RESEARCH_PRIORITY = 1
SOLE_LINK_PRIORITY = 2
REPOSITORY_PRIORITY = 3
DOCUMENT_PRIORITY = 4
NESTED_PRIORITY = 5
DEPRIORITIZED_PRIORITY = 9

REPOSITORY_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org"})
# Hosts whose bare profile and channel pages carry no content worth extracting.
SOCIAL_HOSTS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "medium.com",
        "t.me",
        "telegram.me",
        "threads.net",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "youtube.com",
    }
)
IMAGE_CDN_HOSTS = frozenset(
    {
        "cdn.discordapp.com",
        "i.imgur.com",
        "i.redd.it",
        "media.licdn.com",
        "pbs.twimg.com",
        "preview.redd.it",
        "scontent.cdninstagram.com",
    }
)
STORE_HOSTS = frozenset(
    {
        "amazon.co.uk",
        "amazon.com",
        "amzn.to",
        "buymeacoffee.com",
        "eepurl.com",
        "gumroad.com",
        "patreon.com",
        "shopify.com",
    }
)
HASHTAG_PATH_MARKERS = ("/hashtag/", "/explore/tags/", "/tags/")

TELEGRAM_HOSTS = frozenset({"t.me", "telegram.me", "telegram.dog"})


@dataclass(frozen=True)
class DiscoveredLink:
    """One raw link occurrence, before canonicalization and deduplication."""

    url: str
    from_segment: int | None
    label: str | None
    origin: str


def _bare_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _path_parts(url: str) -> list[str]:
    return [part for part in urlsplit(url).path.split("/") if part]


def _is_tracking_parameter(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMETERS or lowered.startswith(TRACKING_PARAMETER_PREFIXES)


def canonicalize_url(url: str) -> str:
    """Return the comparable form of a URL: one target, one table row."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return url
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in DEFAULT_PORTS or not host:
        return url
    if parsed.username or parsed.password:
        # Credential-bearing URLs are refused downstream; do not rewrite them.
        return url

    netloc = host
    if parsed.port is not None and str(parsed.port) != DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{parsed.port}"
    query = ""
    if parsed.query:
        query = urlencode(
            [
                (name, value)
                for name, value in parse_qsl(parsed.query, keep_blank_values=True)
                if not _is_tracking_parameter(name)
            ]
        )
    return urlunsplit((scheme, netloc, parsed.path, query, parsed.fragment))


def unwrap_url(url: str, *, rounds: int = 3) -> str:
    """Follow outbound redirectors that carry their destination in the URL."""
    current = url
    for _ in range(rounds):
        destination = embedded_destination(current)
        if not destination or destination == current:
            break
        current = destination
    return current


def _with_scheme(value: str) -> str:
    return value if urlsplit(value).scheme else f"https://{value}"


def entity_links(payloads: list[dict[str, Any]]) -> list[DiscoveredLink]:
    """Read link entities, including the hrefs Telegram hides from message text."""
    found: list[DiscoveredLink] = []
    for index, payload in enumerate(sorted(payloads, key=payload_order), 1):
        text = payload.get("text")
        entities = payload.get("entities")
        if text is None:
            text = payload.get("caption")
            entities = payload.get("caption_entities")
        if not isinstance(text, str) or not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            covered = entity_slice(text, entity.get("offset"), entity.get("length"))
            if entity.get("type") == "text_link":
                url, label = entity.get("url"), covered
            elif entity.get("type") == "url":
                url, label = covered, None
            else:
                continue
            if not isinstance(url, str) or not url.strip():
                continue
            found.append(
                DiscoveredLink(
                    url.strip(),
                    index,
                    (label or "").strip() or None,
                    "entity",
                )
            )
    return found


def _segment_boundaries(text: str) -> list[tuple[int, int]]:
    """Return the character offset and number of each rendered segment heading."""
    boundaries = []
    offset = 0
    number = 0
    for line in text.splitlines(keepends=True):
        if SEGMENT_HEADING_RE.fullmatch(line.removesuffix("\n")):
            number += 1
            boundaries.append((offset, number))
        offset += len(line)
    return boundaries


def body_links(text: str) -> list[DiscoveredLink]:
    """Read the links visible in the rendered body, in source order."""
    boundaries = _segment_boundaries(text)
    found = []
    for occurrence in iter_link_occurrences(text):
        segment = None
        for offset, number in boundaries:
            if offset > occurrence.start:
                break
            segment = number
        label = None
        if occurrence.kind == "titled" and occurrence.label_start is not None:
            label = text[occurrence.label_start : occurrence.label_end].strip() or None
        found.append(DiscoveredLink(occurrence.url, segment, label, "body"))
    return found


def is_telegram_channel_url(url: str) -> bool:
    """Return whether a Telegram link points at a channel rather than at a post.

    Forwarded channel posts almost always carry a subscribe link, which is about
    the channel this arrived from and never about what the item is for.
    """
    if _bare_host(url) not in TELEGRAM_HOSTS:
        return False
    parts = [part.lower() for part in _path_parts(url)]
    if not parts or parts[0].startswith("+") or parts[0] == "joinchat":
        return True
    if parts[0] == "s":
        # /s/<channel> is the channel's web preview; /s/<channel>/<id> is a post.
        return len(parts) <= 2
    # /<channel> is the channel itself; /<channel>/<id> is one post inside it.
    return len(parts) == 1


def is_bare_hostname(raw: str, url: str) -> bool:
    """Return whether a link is a domain named in prose rather than shared.

    Telegram marks a bare domain in message text as a link entity, so pasted
    prose citing its sources ("Reddit", "BIKEPACKING.com") arrives looking
    exactly like a URL the user chose to send.
    """
    if urlsplit(raw.strip()).scheme:
        return False
    parsed = urlsplit(url)
    return not parsed.path.strip("/") and not parsed.query


def exclusion_reason(raw: str, url: str) -> str | None:
    """Return why a discovered link is not part of the item, or None to keep it."""
    if is_telegram_channel_url(url):
        return "telegram-channel"
    if is_bare_hostname(raw, url):
        return "bare-hostname"
    return None


def is_repository_url(url: str) -> bool:
    return _bare_host(url) in REPOSITORY_HOSTS and len(_path_parts(url)) >= 2


def is_deprioritized(url: str) -> bool:
    """Return whether a link is title-only regardless of the extraction budget."""
    host = _bare_host(url)
    if host in IMAGE_CDN_HOSTS or host in STORE_HOSTS:
        return True
    path = urlsplit(url).path.lower()
    if any(marker in path for marker in HASHTAG_PATH_MARKERS):
        return True
    # A profile root, a channel, or a bare landing page on a social host.
    return host in SOCIAL_HOSTS and len(_path_parts(url)) <= 1


def link_priority(handler: str, canonical: str, *, sole_link: bool) -> int:
    """Rank which links deserve the extraction budget, per design section 3.3."""
    if handler == "research":
        return RESEARCH_PRIORITY
    if is_deprioritized(canonical):
        return DEPRIORITIZED_PRIORITY
    if sole_link:
        # The other half of rule 2 — the link the item's intent refers to — needs
        # intent detection, which arrives with the index render.
        return SOLE_LINK_PRIORITY
    if is_repository_url(canonical):
        return REPOSITORY_PRIORITY
    if handler in ("medium", "document"):
        return DOCUMENT_PRIORITY
    return NESTED_PRIORITY


def route_target(url: str) -> tuple[str, str]:
    """Select the extractor and its target identity, without touching the network."""
    try:
        route = route_url(url, resolve_redirectors=False)
    except ValueError:
        return "document", ""
    return route.handler, route.identity


def build_link_table(payloads: list[dict[str, Any]], body: str) -> list[LinkTableEntry]:
    """Return the ordered, deduplicated link table for one item."""
    entries: list[LinkTableEntry] = []
    positions: dict[str, int] = {}
    for link in entity_links(payloads) + body_links(body):
        url = _with_scheme(link.url.strip())
        if urlsplit(url).scheme.lower() not in DEFAULT_PORTS:
            continue
        canonical = canonicalize_url(unwrap_url(url))
        if canonical in positions:
            existing = entries[positions[canonical]]
            if existing.label is None and link.label:
                existing.label = link.label
            continue
        excluded = exclusion_reason(link.url, canonical)
        handler, identity = route_target(canonical)
        positions[canonical] = len(entries)
        entries.append(
            LinkTableEntry(
                n=len(entries) + 1,
                raw=link.url,
                canonical=canonical,
                handler=handler,
                identity=identity,
                priority=DEPRIORITIZED_PRIORITY if excluded else DOCUMENT_PRIORITY,
                status="excluded" if excluded else "discovered",
                reason=excluded,
                from_segment=link.from_segment,
                label=link.label,
                origin=link.origin,
            )
        )

    # Excluded rows stay in links.json so a surprising link can still be traced
    # back to where it came from, but they are not part of the item.
    kept = [entry for entry in entries if entry.status != "excluded"]
    sole_link = len(kept) == 1
    for entry in kept:
        entry.priority = link_priority(entry.handler, entry.canonical, sole_link=sole_link)
    return entries


def harvest_entries(
    entries: list[LinkTableEntry],
    harvested: Sequence[HarvestedLink],
    *,
    from_segment: int | None = None,
    limit: int | None = None,
) -> list[LinkTableEntry]:
    """Append the links a finished extraction offers, and return the new rows.

    Nothing the user did not send is worth a row of its own unless it could be
    extracted: a link the item already carries, a store or social link, and a
    channel self-reference are dropped rather than recorded, because a video
    description's sponsorship pile would otherwise swamp the item's link table.
    """
    known = {entry.canonical for entry in entries}
    added: list[LinkTableEntry] = []
    for link in harvested:
        if limit is not None and len(added) >= limit:
            break
        url = _with_scheme(link.url.strip())
        if urlsplit(url).scheme.lower() not in DEFAULT_PORTS:
            continue
        canonical = canonicalize_url(unwrap_url(url))
        if canonical in known:
            continue
        if exclusion_reason(link.url, canonical) or is_deprioritized(canonical):
            continue
        known.add(canonical)
        handler, identity = route_target(canonical)
        entry = LinkTableEntry(
            n=len(entries) + 1,
            raw=link.url,
            canonical=canonical,
            handler=handler,
            identity=identity,
            priority=link_priority(handler, canonical, sole_link=False),
            status="harvested",
            from_segment=from_segment,
            origin="harvest",
            via=link.via,
        )
        entries.append(entry)
        added.append(entry)
    return added
