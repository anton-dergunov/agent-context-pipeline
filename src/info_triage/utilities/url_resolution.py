#!/usr/bin/env python3
"""Resolve URLs and enrich URL-only Markdown with destination titles.

The resolver follows ordinary HTTP redirects and a small set of deterministic
HTML/URL redirect mechanisms.  In particular, it understands the external-link
interstitial currently returned by LinkedIn's lnkd.in shortener.  Title
enrichment fetches public HTML and PDF destinations with strict size bounds.
"""

from __future__ import annotations

import argparse
import html
import io
import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
import unicodedata
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as browser_requests
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .markdown import escape_markdown_destination, escape_markdown_label

URL_RE = re.compile(r"https?://[^\s<>\"\]]+", re.IGNORECASE)
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
REFERENCE_DEFINITION_RE = re.compile(r"^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*", re.MULTILINE)
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>\n]*>")
META_REFRESH_URL_RE = re.compile(r"(?:^|;)\s*url\s*=\s*(.+?)\s*$", re.IGNORECASE)
JS_REDIRECT_RES = (
    re.compile(
        r"(?:window\s*\.\s*)?location(?:\s*\.\s*href)?\s*=\s*"
        r"(?P<quote>['\"])(?P<url>.+?)(?P=quote)\s*;?\s*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:window\s*\.\s*)?location\s*\.\s*replace\s*\(\s*"
        r"(?P<quote>['\"])(?P<url>.+?)(?P=quote)\s*\)\s*;?\s*",
        re.IGNORECASE,
    ),
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)

# Redirectors which put an already usable destination in a query parameter.
# Decoding these first avoids a network call and works when the redirector is
# blocked by authentication or bot protection.
QUERY_REDIRECTORS: dict[str, tuple[str, ...]] = {
    # Google's cookie-consent interstitials, which a European request reaches
    # instead of the page itself. They carry the real destination in continue=.
    "consent.google.com": ("continue",),
    "consent.youtube.com": ("continue",),
    "l.facebook.com": ("u",),
    "lm.facebook.com": ("u",),
    "www.google.com": ("q", "url"),
    "google.com": ("q", "url"),
    "www.youtube.com": ("q",),
    "youtube.com": ("q",),
    "link.zhihu.com": ("target",),
    "steamcommunity.com": ("url",),
    "away.vk.com": ("to",),
    "www.linkedin.com": ("url",),
    "linkedin.com": ("url",),
}

# Common public and branded shorteners.  Resolving only these by default avoids
# rewriting ordinary URLs which happen to redirect to cookie, login, locale, or
# moved-content pages.  --all is available for discovery and unusual domains.
SHORTENER_HOSTS = frozenset(
    {
        "1url.com",
        "2.gp",
        "2pl.us",
        "3.ly",
        "4sq.com",
        "7.ly",
        "9qr.de",
        "a.co",
        "adf.ly",
        "alnk.to",
        "aka.ms",
        "amzn.eu",
        "amzn.to",
        "aol.it",
        "apple.co",
        "b.link",
        "bbc.in",
        "bc.vc",
        "bit.do",
        "bit.ly",
        "bitly.com",
        "bl.ink",
        "brlnk.io",
        "buff.ly",
        "cli.re",
        "cnn.it",
        "cos.lv",
        "cutt.ly",
        "cutt.us",
        "db.tt",
        "detne.ws",
        "dlvr.it",
        "econ.st",
        "eepurl.com",
        "fb.me",
        "flip.it",
        "forms.gle",
        "fur.ly",
        "g.co",
        "gg.gg",
        "git.io",
        "go2l.ink",
        "goo.gl",
        "goo.gle",
        "ht.ly",
        "hubs.la",
        "hubs.li",
        "huff.to",
        "ift.tt",
        "is.gd",
        "ity.im",
        "j.mp",
        "lat.ms",
        "lc.chat",
        "lnk.bio",
        "lnk.to",
        "lnkd.in",
        "mcaf.ee",
        "mzl.la",
        "n.pr",
        "nyer.cm",
        "nyti.ms",
        "on.freep.com",
        "ow.ly",
        "p4k.in",
        "qr.ae",
        "rb.gy",
        "read.bi",
        "rebrand.ly",
        "rol.st",
        "s.id",
        "say.ly",
        "shar.es",
        "shiny.link",
        "short.io",
        "shorturl.at",
        "shrtco.de",
        "snip.ly",
        "soo.gd",
        "spoti.fi",
        "stanford.io",
        "t.co",
        "t.ly",
        "thebea.st",
        "therin.gr",
        "tiny.cc",
        "tiny.one",
        "tinyurl.com",
        "tmblr.co",
        "trib.in",
        "v.gd",
        "wa.link",
        "wapo.st",
        "wp.me",
        "x.co",
        "yousend.it",
        "youtu.be",
    }
)
SHORTENER_HOST_SUFFIXES = (".trib.al", ".safelinks.protection.outlook.com")
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
MAX_TITLE_CHARACTERS = 500
GENERIC_ERROR_TITLES = frozenset(
    {
        "access denied",
        "amazon.com",
        "amazon.co.uk",
        "are you a robot?",
        "attention required! | cloudflare",
        "before you continue to youtube",
        "client challenge",
        "forbidden",
        "instagram",
        "just a moment...",
        "not found",
        "page not found",
        "perplexity",
        "sciencedirect",
        "spotify",
        "- youtube",
    }
)

PDF_FILENAME_TITLE_RE = re.compile(
    r"(?:^|\s)(?:microsoft (?:word|powerpoint)\s*-\s*)?.+\.(?:dvi|docx?|pdf|pptx?|ps|tex)$",
    re.IGNORECASE,
)
PDF_LIGATURES = str.maketrans(
    {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
    }
)


@dataclass(frozen=True)
class Resolution:
    url: str
    succeeded: bool
    error: str | None = None


@dataclass(frozen=True)
class LinkOccurrence:
    """One URL-bearing construct and the spans needed to rewrite it safely."""

    url: str
    start: int
    end: int
    span_start: int
    span_end: int
    kind: str
    label_start: int | None = None
    label_end: int | None = None
    eligible_for_title: bool = True


@dataclass(frozen=True)
class LinkResolution:
    """Best known destination, optional title, and an expected problem."""

    url: str
    title: str | None
    reason: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.reason is None


CacheValue = str | dict[str, str]


def make_session(retries: int) -> requests.Session:
    """Return a session with conservative retries for transient failures."""
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        backoff_factor=1.0,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-GB,en;q=0.9",
        }
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.max_redirects = 20
    return session


def make_browser_session() -> browser_requests.Session:
    """Return the Chrome-compatible HTTP client used by the Medium extractor."""
    return browser_requests.Session(
        impersonate="chrome",
        headers={"Accept-Language": "en-GB,en;q=0.9"},
    )


def _decoded_http_url(value: str) -> str | None:
    """Decode a URL-valued field without interpreting arbitrary page text."""
    candidate = html.unescape(value).strip().strip("'\"")
    for _ in range(3):
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    if urlsplit(candidate).scheme.lower() in {"http", "https"}:
        return candidate
    return None


def embedded_destination(url: str) -> str | None:
    """Extract destinations embedded by well-known outbound redirectors."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()

    # href.li uses the entire query string as its target, without a key.
    if host == "href.li" and parsed.query:
        return _decoded_http_url(parsed.query)

    # Microsoft Safe Links is deployed on many regional subdomains.
    if host.endswith(".safelinks.protection.outlook.com"):
        values = parse_qs(parsed.query).get("url", ())
        return _decoded_http_url(values[0]) if values else None

    keys = QUERY_REDIRECTORS.get(host)
    if not keys:
        return None

    # Restrict broad hosts to their actual redirect endpoints.
    if host in {"www.google.com", "google.com"} and parsed.path != "/url":
        return None
    if host in {"l.facebook.com", "lm.facebook.com"} and parsed.path != "/l.php":
        return None
    if host in {"www.youtube.com", "youtube.com"} and parsed.path != "/redirect":
        return None
    if host in {"www.linkedin.com", "linkedin.com"} and parsed.path != "/safety/go":
        return None

    query = parse_qs(parsed.query)
    for key in keys:
        values = query.get(key, ())
        if values:
            destination = _decoded_http_url(values[0])
            if destination:
                return destination
    return None


def is_redirector_url(url: str) -> bool:
    """Return whether URL should be resolved in the safe default mode."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in SHORTENER_HOSTS or any(
        host == suffix[1:] or host.endswith(suffix) for suffix in SHORTENER_HOST_SUFFIXES
    ):
        return True
    return embedded_destination(url) is not None


def _normalized_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _read_response_body(response: requests.Response, limit: int) -> bytes:
    """Read at most limit bytes so a URL cannot force a huge download."""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        remaining = limit - size
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        size += min(len(chunk), remaining)
        if size >= limit:
            break
    return b"".join(chunks)


def _html_destination(body: bytes, response: requests.Response) -> str | None:
    """Extract a destination from supported deterministic HTML redirects."""
    content_type = response.headers.get("Content-Type", "").lower()
    if "html" not in content_type and not body.lstrip().lower().startswith(b"<!doctype html"):
        return None

    encoding = response.encoding or "utf-8"
    text = body.decode(encoding, errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    host = (urlsplit(response.url).hostname or "").lower()

    # lnkd.in now returns a safety interstitial instead of an HTTP redirect.
    # This semantic attribute is substantially less brittle than CSS classes or
    # visible text, and it does not use the title surrounding the original URL.
    if host == "lnkd.in" or host.endswith(".linkedin.com"):
        link = soup.select_one('a[data-tracking-control-name="external_url_click"][href]')
        if link:
            return urljoin(response.url, html.unescape(str(link["href"])))

    # AvantLink's public shortener currently stops at a consent page. Its GET
    # form explicitly offers a no-cookie route, which can be followed without
    # accepting tracking or executing page scripts. Keep this narrowly scoped
    # to the known host and expected hidden fields rather than submitting
    # arbitrary forms found on the web.
    if host == "classic.avantlink.com":
        form = soup.find(
            "form", action=True, method=lambda value: not value or value.lower() == "get"
        )
        if form:
            fields = {
                str(element.get("name")): str(element.get("value", ""))
                for element in form.find_all("input", attrs={"name": True})
                if element.get("type", "").lower() == "hidden"
            }
            if fields.get("cookie_consent") == "1" and fields.get("shortened_url_id"):
                fields["no_consent_checkbox"] = "on"
                action = urljoin(response.url, html.unescape(str(form["action"])))
                separator = "&" if urlsplit(action).query else "?"
                return f"{action}{separator}{urlencode(fields)}"

    for meta in soup.find_all("meta"):
        equiv = meta.get("http-equiv")
        content = meta.get("content")
        if not equiv or not content or str(equiv).lower() != "refresh":
            continue
        match = META_REFRESH_URL_RE.search(str(content))
        if match:
            return urljoin(
                response.url,
                html.unescape(match.group(1).strip().strip("'\"")),
            )

    # Some legacy shorteners use a literal JavaScript navigation.  Only parse
    # direct string assignments/calls; never execute JavaScript.
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text(" ")
        for pattern in JS_REDIRECT_RES:
            # Requiring the entire script element to be a navigation prevents
            # false positives from ordinary application code and examples.
            match = pattern.fullmatch(script_text.strip())
            if match:
                return urljoin(response.url, html.unescape(match.group("url")))

    return None


def resolve_url(
    url: str,
    session: requests.Session,
    *,
    timeout: float,
    max_html_bytes: int,
    max_rounds: int = 10,
) -> Resolution:
    """Resolve one URL through network and page-level redirect mechanisms."""
    original = html.unescape(url)
    current = original
    visits: Counter[str] = Counter()

    try:
        for _ in range(max_rounds):
            visits[current] += 1
            if visits[current] > 2:
                return Resolution(current, False, "redirect loop detected")

            embedded = embedded_destination(current)
            if embedded and not visits[embedded]:
                current = embedded
                continue

            if unsafe := _unsafe_url_message(current):
                return Resolution(current, False, f"unsafe URL: {unsafe}")

            with closing(
                session.get(
                    current,
                    allow_redirects=False,
                    timeout=(min(timeout, 15.0), timeout),
                    stream=True,
                )
            ) as response:
                network_url = response.url or current
                if unsafe := _unsafe_url_message(network_url):
                    return Resolution(current, False, f"unsafe URL: {unsafe}")
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        return Resolution(
                            network_url,
                            False,
                            "redirect response did not provide a destination",
                        )
                    current = urljoin(network_url, location)
                    continue
                if response.status_code >= 400:
                    return Resolution(network_url, False, f"HTTP {response.status_code}")
                body = _read_response_body(response, max_html_bytes)
                page_url = _html_destination(body, response)

            if page_url and page_url != current:
                current = page_url
                continue
            if is_redirector_url(current):
                partial = (
                    current if _normalized_host(current) != _normalized_host(original) else original
                )
                return Resolution(
                    partial,
                    False,
                    "shortener chain did not expose a final destination "
                    "(the last link may be expired)",
                )
            return Resolution(network_url, True)

        return Resolution(current, False, f"exceeded {max_rounds} page-level redirects")
    except requests.RequestException as exc:
        # Preserve a target extracted during an earlier page-level/embedded hop,
        # even if fetching that target fails. Requests does not expose an HTTP
        # redirect target when it raises while following the redirect itself.
        partial = current if current != original else original
        return Resolution(partial, False, str(exc))


def _split_url_and_punctuation(match: re.Match[str]) -> tuple[str, str]:
    """Keep prose/Markdown punctuation outside the URL replacement."""
    value = match.group(0)
    suffix = ""
    while value and value[-1] in ".,;:!?'":
        suffix = value[-1] + suffix
        value = value[:-1]
    while value.endswith(")") and value.count(")") > value.count("("):
        suffix = ")" + suffix
        value = value[:-1]
    while value.endswith("}") and value.count("}") > value.count("{"):
        suffix = "}" + suffix
        value = value[:-1]
    return value, suffix


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _fenced_code_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    opening: tuple[str, int, int] | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        match = FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if opening is None:
                opening = (marker[0], len(marker), offset)
            elif marker[0] == opening[0] and len(marker) >= opening[1]:
                ranges.append((opening[2], offset + len(line)))
                opening = None
        offset += len(line)
    if opening is not None:
        ranges.append((opening[2], len(text)))
    return ranges


def _inline_code_ranges(text: str, protected: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        start = text.find("`", index)
        if start < 0:
            break
        if _overlaps(start, start + 1, protected):
            index = start + 1
            continue
        marker_end = start
        while marker_end < len(text) and text[marker_end] == "`":
            marker_end += 1
        marker = text[start:marker_end]
        end = text.find(marker, marker_end)
        if end < 0 or _overlaps(end, end + len(marker), protected):
            index = marker_end
            continue
        ranges.append((start, end + len(marker)))
        index = end + len(marker)
    return ranges


def _markdown_link_at(text: str, start: int) -> LinkOccurrence | None:
    image = start > 0 and text[start - 1] == "!"
    label_end = text.find("](", start + 1)
    if label_end < 0 or "\n" in text[start:label_end]:
        return None
    label = text[start + 1 : label_end]
    cursor = label_end + 2
    if cursor >= len(text):
        return None

    angle_wrapped = text[cursor] == "<"
    if angle_wrapped:
        url_start = cursor + 1
        url_end = text.find(">", url_start)
        if url_end < 0:
            return None
        cursor = url_end + 1
    else:
        url_start = cursor
        depth = 0
        while cursor < len(text):
            char = text[cursor]
            if char == "\n":
                return None
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            elif char.isspace() and depth == 0:
                break
            cursor += 1
        url_end = cursor

    url = text[url_start:url_end]
    if not URL_RE.fullmatch(url):
        return None

    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1
    if cursor < len(text) and text[cursor] in "\"'":
        quote = text[cursor]
        cursor += 1
        title_end = text.find(quote, cursor)
        if title_end < 0:
            return None
        cursor = title_end + 1
        while cursor < len(text) and text[cursor] in " \t":
            cursor += 1
    if cursor >= len(text) or text[cursor] != ")":
        return None

    span_start = start - 1 if image else start
    if image:
        kind = "image"
        eligible = False
    elif label == url:
        kind = "markdown"
        eligible = True
    else:
        kind = "titled"
        eligible = False
    return LinkOccurrence(
        url=url,
        start=url_start,
        end=url_end,
        span_start=span_start,
        span_end=cursor + 1,
        kind=kind,
        label_start=start + 1,
        label_end=label_end,
        eligible_for_title=eligible,
    )


def iter_link_occurrences(text: str) -> Iterable[LinkOccurrence]:
    """Yield Markdown-aware URL occurrences in source order."""
    protected = _fenced_code_ranges(text)
    protected.extend(_inline_code_ranges(text, protected))
    occurrences: list[LinkOccurrence] = []

    index = 0
    while True:
        start = text.find("[", index)
        if start < 0:
            break
        occurrence = (
            None if _overlaps(start, start + 1, protected) else _markdown_link_at(text, start)
        )
        if occurrence is None:
            index = start + 1
            continue
        occurrences.append(occurrence)
        protected.append((occurrence.span_start, occurrence.span_end))
        index = occurrence.span_end

    for match in re.finditer(r"<(https?://[^<>\s]+)>", text, re.IGNORECASE):
        if _overlaps(match.start(), match.end(), protected):
            continue
        occurrences.append(
            LinkOccurrence(
                url=match.group(1),
                start=match.start(1),
                end=match.end(1),
                span_start=match.start(),
                span_end=match.end(),
                kind="autolink",
            )
        )
        protected.append((match.start(), match.end()))

    for match in HTML_TAG_RE.finditer(text):
        if not _overlaps(match.start(), match.end(), protected):
            protected.append((match.start(), match.end()))

    for definition in REFERENCE_DEFINITION_RE.finditer(text):
        line_end = text.find("\n", definition.end())
        if line_end < 0:
            line_end = len(text)
        cursor = definition.end()
        angle_wrapped = cursor < len(text) and text[cursor] == "<"
        if angle_wrapped:
            cursor += 1
        match = URL_RE.match(text, cursor, line_end)
        if not match:
            continue
        url, _ = _split_url_and_punctuation(match)
        end = cursor + len(url)
        occurrences.append(
            LinkOccurrence(
                url=url,
                start=cursor,
                end=end,
                span_start=cursor,
                span_end=end,
                kind="reference",
                eligible_for_title=False,
            )
        )
        protected.append((cursor, end))

    for match in URL_RE.finditer(text):
        if _overlaps(match.start(), match.end(), protected):
            continue
        url, _ = _split_url_and_punctuation(match)
        if not url:
            continue
        end = match.start() + len(url)
        occurrences.append(
            LinkOccurrence(
                url=url,
                start=match.start(),
                end=end,
                span_start=match.start(),
                span_end=end,
                kind="bare",
            )
        )

    yield from sorted(occurrences, key=lambda item: (item.span_start, item.start))


def iter_urls(text: str) -> Iterable[str]:
    """Yield URL destinations while ignoring code and raw HTML attributes."""
    for occurrence in iter_link_occurrences(text):
        yield occurrence.url


def replace_url_destinations(text: str, transform: Callable[[str], str]) -> str:
    """Rewrite URL destinations while preserving their surrounding Markdown."""
    replacements: dict[tuple[int, int], str] = {}
    for occurrence in iter_link_occurrences(text):
        replacement = transform(occurrence.url)
        replacements[(occurrence.start, occurrence.end)] = replacement
        if occurrence.kind == "markdown":
            replacements[(occurrence.label_start, occurrence.label_end)] = replacement
    for (start, end), replacement in sorted(replacements.items(), reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def replace_urls(
    text: str,
    resolver: "URLResolver",
) -> str:
    return replace_url_destinations(text, resolver.resolve)


def enrich_links(text: str, resolver: "URLResolver") -> str:
    """Replace bare and URL-labelled links with titled Markdown links."""
    occurrences = [item for item in iter_link_occurrences(text) if item.eligible_for_title]
    results: dict[str, LinkResolution] = {}
    for occurrence in occurrences:
        if occurrence.url not in results:
            results[occurrence.url] = resolver.resolve_link(occurrence.url)

    for occurrence in reversed(occurrences):
        result = results[occurrence.url]
        if result.title:
            replacement = (
                f"[{escape_markdown_label(result.title)}]"
                f"({escape_markdown_destination(result.url)})"
            )
        else:
            replacement = result.url
        text = text[: occurrence.span_start] + replacement + text[occurrence.span_end :]
    return text


def _normalize_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    title = " ".join(html.unescape(value).split())
    if not title or len(title) > MAX_TITLE_CHARACTERS:
        return None
    if any(unicodedata.category(character).startswith("C") for character in title):
        return None
    if title.casefold() in GENERIC_ERROR_TITLES or title.casefold().startswith("404 "):
        return None
    return title


def _title_matches_host(title: str, url: str) -> bool:
    """Reject labels which only repeat the destination's host name."""
    host = _normalized_host(url).rstrip(".")
    normalized = title.casefold().removeprefix("www.").rstrip("./ ")
    return normalized == host


def _json_ld_title(soup: BeautifulSoup) -> str | None:
    accepted_types = {"article", "blogposting", "creativework", "newsarticle", "webpage"}
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            value = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        pending = [value]
        visited = 0
        while pending and visited < 1_000:
            item = pending.pop()
            visited += 1
            if isinstance(item, list):
                pending.extend(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            if isinstance(item_type, str):
                types = {item_type}
            elif isinstance(item_type, (list, tuple)):
                types = {kind for kind in item_type if isinstance(kind, str)}
            else:
                types = set()
            if {str(kind).casefold() for kind in types} & accepted_types:
                for key in ("headline", "name"):
                    if title := _normalize_title(item.get(key)):
                        return title
            pending.extend(item.values())
    return None


def _html_title(body: bytes, response: requests.Response) -> str | None:
    encoding = response.encoding or "utf-8"
    soup = BeautifulSoup(body.decode(encoding, errors="replace"), "html.parser")
    selectors = (
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
        'meta[property="twitter:title"]',
    )
    for selector in selectors:
        element = soup.select_one(selector)
        if (
            element
            and (title := _normalize_title(element.get("content")))
            and not _title_matches_host(title, response.url)
        ):
            return title
    if (title := _json_ld_title(soup)) and not _title_matches_host(title, response.url):
        return title
    if (
        soup.title
        and (title := _normalize_title(soup.title.get_text(" ")))
        and not _title_matches_host(title, response.url)
    ):
        return title
    heading = soup.find("h1")
    if (
        heading
        and (title := _normalize_title(heading.get_text(" ")))
        and not _title_matches_host(title, response.url)
    ):
        return title
    return None


def _normalize_pdf_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return _normalize_title(unicodedata.normalize("NFKC", value.translate(PDF_LIGATURES)))


def _pdf_metadata_title(value: object) -> str | None:
    title = _normalize_pdf_text(value)
    if not title or PDF_FILENAME_TITLE_RE.search(title) or title.casefold() == "untitled":
        return None
    return title


def _join_pdf_fragments(fragments: list[tuple[str, float, float, float, int]]) -> str | None:
    """Infer the title from the largest, compact text block on page one."""
    usable = [fragment for fragment in fragments if fragment[0].strip() and fragment[1] > 0]
    if not usable:
        return None
    sizes = [fragment[1] for fragment in usable]
    largest = max(sizes)
    if largest - min(sizes) < 1.0:
        return None

    prominent = [fragment for fragment in usable if fragment[1] >= largest * 0.8]
    rows = sorted({round(fragment[3], 1) for fragment in prominent}, reverse=True)
    clusters: list[list[float]] = []
    for row in rows:
        if not clusters or clusters[-1][-1] - row > 28:
            clusters.append([row])
        else:
            clusters[-1].append(row)

    candidates: list[tuple[float, int, str]] = []
    for cluster in clusters:
        upper, lower = cluster[0] + 1, cluster[-1] - 1
        selected = [fragment for fragment in prominent if lower <= round(fragment[3], 1) <= upper]
        selected.sort(key=lambda fragment: fragment[4])
        value = " ".join(fragment[0].strip() for fragment in selected)
        value = re.sub(r"\bO\s+VERVIEW\b", "OVERVIEW", value)
        if title := _normalize_pdf_text(value):
            if len(title) >= 12 and not title.casefold().startswith("arxiv:"):
                candidates.append((max(fragment[1] for fragment in selected), len(title), title))
    return max(candidates, default=(0, 0, None), key=lambda item: (item[0], item[1]))[2]


def _pdf_page_title(reader: PdfReader) -> str | None:
    if not reader.pages:
        return None
    page = reader.pages[0]
    fragments: list[tuple[str, float, float, float, int]] = []

    def visitor(
        text: str,
        _current_matrix: list[float],
        text_matrix: list[float],
        _font: dict[str, Any] | None,
        font_size: float,
    ) -> None:
        for line in text.splitlines() or [text]:
            if line.strip():
                fragments.append(
                    (
                        line,
                        float(font_size),
                        float(text_matrix[4]),
                        float(text_matrix[5]),
                        len(fragments),
                    )
                )

    lines = [
        normalized
        for line in (page.extract_text(visitor_text=visitor) or "").splitlines()
        if (normalized := _normalize_pdf_text(line))
    ]
    if title := _join_pdf_fragments(fragments):
        if lines and title.startswith(lines[0]) and len(lines) > 1:
            possible_authors = lines[1]
            if possible_authors.count(",") >= 2:
                return lines[0]
        return title

    # Some generated PDFs expose no usable font-size information. In those
    # documents, the first one or two short lines before prose are the most
    # conservative text-based title candidate.
    candidates: list[str] = []
    for line in lines[:4]:
        lowered = line.casefold()
        if candidates and (
            lowered.startswith(("abstract", "authors?", "keywords"))
            or "@" in line
            or line.count(",") >= 2
            or len(line) > 120
            or (len(line) > 60 and line.endswith((".", ":")))
        ):
            break
        candidates.append(line)
        if len(candidates) == 2:
            break
    return _normalize_pdf_text(" ".join(candidates)) if candidates else None


def _pdf_title(body: bytes) -> tuple[str | None, str | None]:
    try:
        reader = PdfReader(io.BytesIO(body), strict=False)
        metadata = reader.metadata
        candidates: list[object] = []
        if metadata is not None:
            candidates.extend((getattr(metadata, "title", None), metadata.get("/Title")))
        xmp = reader.xmp_metadata
        if xmp is not None:
            dc_title = getattr(xmp, "dc_title", None)
            if isinstance(dc_title, dict):
                candidates.extend(dc_title.values())
            elif isinstance(dc_title, (list, tuple)):
                candidates.extend(dc_title)
            else:
                candidates.append(dc_title)
        for candidate in candidates:
            if title := _pdf_metadata_title(candidate):
                return title, None
        return _pdf_page_title(reader), None
    except Exception as error:  # pypdf exposes several parser-specific exception classes
        return None, f"could not parse PDF metadata: {error}"


def _unsafe_url_message(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        return str(error)
    if parsed.scheme.lower() not in {"http", "https"}:
        return "only HTTP and HTTPS URLs are supported"
    if not parsed.hostname:
        return "URL has no hostname"
    if parsed.username is not None or parsed.password is not None:
        return "credential-bearing URLs are not allowed"
    if port is not None and not 1 <= port <= 65535:
        return "URL port is outside the valid range"

    host = parsed.hostname.rstrip(".")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            return f"hostname lookup failed: {error}"
        addresses = {ipaddress.ip_address(item[4][0].split("%", 1)[0]) for item in resolved}
    if not addresses or any(not address.is_global for address in addresses):
        return "destination does not resolve exclusively to public addresses"
    return None


def public_url_error(url: str) -> str | None:
    """Return why a URL is unsafe to fetch, or ``None`` for a public HTTP(S) URL."""
    return _unsafe_url_message(url)


def _read_bounded_body(
    response: requests.Response,
    limit: int,
) -> tuple[bytes | None, str | None]:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > limit:
                return None, f"response exceeded {limit} bytes"
        except ValueError:
            pass
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > limit:
            return None, f"response exceeded {limit} bytes"
        chunks.append(chunk)
    return b"".join(chunks), None


def _looks_like_html(body: bytes) -> bool:
    prefix = body[:4096].lstrip().lower()
    return prefix.startswith((b"<!doctype html", b"<html")) or b"<head" in prefix


def _resolve_link_once(
    url: str,
    session: Any,
    *,
    timeout: float,
    max_html_bytes: int,
    max_pdf_bytes: int,
    max_rounds: int = 10,
) -> LinkResolution:
    current = html.unescape(url)
    visits: Counter[str] = Counter()

    try:
        for _ in range(max_rounds):
            visits[current] += 1
            if visits[current] > 2:
                return LinkResolution(current, None, "redirect-loop", "redirect loop detected")

            embedded = embedded_destination(current)
            if embedded and not visits[embedded]:
                current = embedded
                continue

            if unsafe := _unsafe_url_message(current):
                return LinkResolution(current, None, "unsafe-url", f"unsafe URL: {unsafe}")

            with closing(
                session.get(
                    current,
                    allow_redirects=False,
                    timeout=(min(timeout, 15.0), timeout),
                    stream=True,
                )
            ) as response:
                response_url = response.url or current
                if unsafe := _unsafe_url_message(response_url):
                    return LinkResolution(
                        current,
                        None,
                        "unsafe-url",
                        f"unsafe URL: {unsafe}",
                    )
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        return LinkResolution(
                            response_url,
                            None,
                            "destination-not-found",
                            "redirect response did not provide a destination",
                        )
                    current = urljoin(response_url, location)
                    continue
                if response.status_code >= 400:
                    return LinkResolution(
                        response_url,
                        None,
                        "http-error",
                        f"HTTP {response.status_code}",
                    )

                content_type = response.headers.get("Content-Type", "").lower()
                is_pdf = "application/pdf" in content_type or urlsplit(
                    response_url
                ).path.lower().endswith(".pdf")
                limit = max_pdf_bytes if is_pdf else max_html_bytes
                body, size_error = _read_bounded_body(response, limit)
                if size_error:
                    return LinkResolution(
                        response_url,
                        None,
                        "response-too-large",
                        size_error,
                    )
                assert body is not None

                if is_pdf:
                    title, pdf_error = _pdf_title(body)
                    if pdf_error:
                        return LinkResolution(
                            response_url,
                            None,
                            "unsupported-content",
                            pdf_error,
                        )
                    if title and title != response_url:
                        return LinkResolution(response_url, title)
                    return LinkResolution(
                        response_url,
                        None,
                        "title-not-found",
                        "no trustworthy title found",
                    )

                if not any(kind in content_type for kind in HTML_CONTENT_TYPES) and not (
                    not content_type and _looks_like_html(body)
                ):
                    return LinkResolution(
                        response_url,
                        None,
                        "unsupported-content",
                        f"unsupported content type: {content_type or 'unknown'}",
                    )

                page_url = _html_destination(body, response)
                if page_url and page_url != current:
                    current = page_url
                    continue
                if is_redirector_url(current):
                    return LinkResolution(
                        response_url,
                        None,
                        "destination-not-found",
                        "shortener chain did not expose a final destination "
                        "(the last link may be expired)",
                    )
                if (title := _html_title(body, response)) and title != response_url:
                    return LinkResolution(response_url, title)
                return LinkResolution(
                    response_url,
                    None,
                    "title-not-found",
                    "no trustworthy title found",
                )
        return LinkResolution(
            current,
            None,
            "redirect-limit",
            f"exceeded {max_rounds} page-level redirects",
        )
    except (requests.RequestException, browser_requests.RequestsError) as error:
        return LinkResolution(current, None, "request-error", str(error))


def resolve_link(
    url: str,
    session: Any,
    *,
    timeout: float,
    max_html_bytes: int,
    max_pdf_bytes: int,
    max_rounds: int = 10,
    browser_session: Any | None = None,
) -> LinkResolution:
    """Resolve one public URL, retrying blocked pages with browser-compatible HTTP."""
    result = _resolve_link_once(
        url,
        session,
        timeout=timeout,
        max_html_bytes=max_html_bytes,
        max_pdf_bytes=max_pdf_bytes,
        max_rounds=max_rounds,
    )
    if (
        result.title
        or browser_session is None
        or result.reason
        in {
            "unsafe-url",
            "unsupported-content",
        }
    ):
        return result

    browser_result = _resolve_link_once(
        url,
        browser_session,
        timeout=timeout,
        max_html_bytes=max_html_bytes,
        max_pdf_bytes=max_pdf_bytes,
        max_rounds=max_rounds,
    )
    if browser_result.title:
        return browser_result
    quality = {
        "title-not-found": 4,
        "response-too-large": 3,
        "unsupported-content": 3,
        "destination-not-found": 2,
        "http-error": 1,
        "request-error": 1,
        "redirect-loop": 1,
        "redirect-limit": 1,
        "unsafe-url": 0,
    }
    if quality.get(browser_result.reason, 0) > quality.get(result.reason, 0):
        return browser_result
    if (
        result.url == url
        and browser_result.url != url
        and not is_redirector_url(browser_result.url)
    ):
        return browser_result
    return result


def _cached_url(value: CacheValue | None) -> str | None:
    if isinstance(value, str):
        return value
    return value.get("url") if isinstance(value, dict) else None


class URLResolver:
    """Resolve URLs and titles while sharing an HTTP session and caches."""

    def __init__(
        self,
        *,
        timeout: float,
        retries: int,
        max_html_bytes: int,
        max_pdf_bytes: int = 20 * 1024 * 1024,
        cache: dict[str, CacheValue] | None = None,
        verbose: bool = False,
        resolve_all: bool = False,
    ) -> None:
        self.timeout = timeout
        self.max_html_bytes = max_html_bytes
        self.max_pdf_bytes = max_pdf_bytes
        self.cache = cache if cache is not None else {}
        self.verbose = verbose
        self.resolve_all = resolve_all
        self.session = make_session(retries)
        self.browser_session = make_browser_session()
        self.failures: list[tuple[str, str]] = []
        self.failure_reasons: list[str] = []
        self._link_results: dict[str, LinkResolution] = {}

    @property
    def link_results(self) -> dict[str, LinkResolution]:
        return dict(self._link_results)

    def _record_failure(self, url: str, reason: str, message: str) -> None:
        self.failures.append((url, message))
        self.failure_reasons.append(reason)
        print(f"warning: could not enrich {url}: {message}", file=sys.stderr)

    def resolve(self, url: str) -> str:
        if not self.resolve_all and not is_redirector_url(url):
            return url
        if cached := _cached_url(self.cache.get(url)):
            # Do not perpetuate a stale/partial cache entry which still points
            # at a recognized shortener; retry it instead.
            if not is_redirector_url(cached):
                return cached
        result = resolve_url(
            url,
            self.session,
            timeout=self.timeout,
            max_html_bytes=self.max_html_bytes,
        )
        if result.succeeded:
            self.cache[url] = {"url": result.url}
            if self.verbose and result.url != url:
                print(f"{url} -> {result.url}", file=sys.stderr)
        else:
            message = result.error or "unknown resolution error"
            self._record_failure(url, "request-error", message)
        return result.url

    def resolve_link(self, url: str) -> LinkResolution:
        if url in self._link_results:
            return self._link_results[url]

        cached = self.cache.get(url)
        cached_title = _normalize_title(cached.get("title")) if isinstance(cached, dict) else None
        if isinstance(cached, dict) and cached.get("url") and cached_title:
            result = LinkResolution(cached["url"], cached_title)
            self._link_results[url] = result
            return result

        # A titleless cache entry may contain an interstitial or incomplete
        # redirect destination from an older run. Retry from the source URL so
        # improved redirect/browser handling can recover the full chain.
        start_url = url
        result = resolve_link(
            start_url,
            self.session,
            timeout=self.timeout,
            max_html_bytes=self.max_html_bytes,
            max_pdf_bytes=self.max_pdf_bytes,
            browser_session=self.browser_session,
        )
        self._link_results[url] = result
        cache_value = {"url": result.url}
        if result.title:
            cache_value["title"] = result.title
        self.cache[url] = cache_value
        if result.reason:
            self._record_failure(
                url,
                result.reason,
                result.error or "unknown title-resolution error",
            )
        elif self.verbose:
            print(f"{url} -> {result.url}", file=sys.stderr)
        return result


def load_cache(path: Path | None) -> dict[str, CacheValue]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("version") == 2:
        data = data.get("links")
    if not isinstance(data, dict):
        raise ValueError(f"cache must contain a JSON object: {path}")
    cache: dict[str, CacheValue] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise ValueError(f"cache keys must be URLs: {path}")
        if isinstance(value, str):
            cache[key] = value
        elif (
            isinstance(value, dict)
            and isinstance(value.get("url"), str)
            and all(
                item in {"url", "title"} and isinstance(field, str) for item, field in value.items()
            )
        ):
            cache[key] = dict(value)
        else:
            raise ValueError(f"cache entries must contain URL and optional title: {path}")
    return cache


def serialize_cache(cache: dict[str, CacheValue]) -> str:
    links = {
        key: ({"url": value} if isinstance(value, str) else value) for key, value in cache.items()
    }
    return (
        json.dumps(
            {"version": 2, "links": links},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def serialize_report(results: dict[str, LinkResolution]) -> str:
    reasons = Counter(result.reason or "resolved" for result in results.values())
    return (
        json.dumps(
            {
                "summary": {
                    "total": len(results),
                    "titled": sum(result.title is not None for result in results.values()),
                    "untitled": sum(result.title is None for result in results.values()),
                    "outcomes": dict(sorted(reasons.items())),
                },
                "results": [
                    {
                        "source_url": source_url,
                        "final_url": result.url,
                        "title": result.title,
                        "reason": result.reason,
                        "error": result.error,
                    }
                    for source_url, result in results.items()
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(text)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 text file to process")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("-o", "--output", type=Path, help="write to this file")
    destination.add_argument(
        "-i", "--in-place", action="store_true", help="atomically replace the input file"
    )
    parser.add_argument(
        "--cache", type=Path, help="optional persistent JSON cache (read and update)"
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="write a JSON report for every title-resolution attempt",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="read timeout in seconds")
    parser.add_argument("--retries", type=int, default=4, help="transient network retries")
    parser.add_argument(
        "--max-html-bytes",
        type=int,
        default=20 * 1024 * 1024,
        help="maximum HTML response bytes inspected for redirects and titles",
    )
    parser.add_argument(
        "--max-pdf-bytes",
        type=int,
        default=20 * 1024 * 1024,
        help="maximum PDF bytes inspected for metadata and first-page title text",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show resolved URLs")
    parser.add_argument(
        "--all",
        action="store_true",
        help="with --urls-only, follow hosts not recognized as shorteners",
    )
    parser.add_argument(
        "--urls-only",
        action="store_true",
        help="resolve URL destinations without adding Markdown titles",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit nonzero if any selected URL could not be fully resolved",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.retries < 0:
        parser.error("--retries cannot be negative")
    if args.max_html_bytes <= 0:
        parser.error("--max-html-bytes must be positive")
    if args.max_pdf_bytes <= 0:
        parser.error("--max-pdf-bytes must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    text = args.input.read_text(encoding="utf-8")
    cache = load_cache(args.cache)
    resolver = URLResolver(
        timeout=args.timeout,
        retries=args.retries,
        max_html_bytes=args.max_html_bytes,
        max_pdf_bytes=args.max_pdf_bytes,
        cache=cache,
        verbose=args.verbose,
        resolve_all=args.all,
    )
    output = replace_urls(text, resolver) if args.urls_only else enrich_links(text, resolver)

    if args.in_place:
        atomic_write(args.input, output)
    elif args.output:
        atomic_write(args.output, output)
    else:
        sys.stdout.write(output)

    if args.cache is not None:
        atomic_write(args.cache, serialize_cache(resolver.cache))
    if args.report is not None:
        atomic_write(args.report, serialize_report(resolver.link_results))
    return 1 if args.strict and resolver.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
