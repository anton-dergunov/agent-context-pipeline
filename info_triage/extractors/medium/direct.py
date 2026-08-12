"""Best-effort direct Medium retrieval with a Chrome-compatible HTTP stack."""

from __future__ import annotations

import http.cookiejar
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import browser_cookie3
from bs4 import BeautifulSoup
from curl_cffi import requests

from .postprocess import html_to_markdown
from .urls import ArticleReference

MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 10 * 1024 * 1024
XSSI_PREFIX = "]) }while(1);</x>".replace(" ", "")


@dataclass(slots=True)
class DirectError(RuntimeError):
    message: str
    kind: str = "failed"

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class DirectArticle:
    article_id: str
    title: str
    url: str
    author: str | None
    username: str | None
    published_at: int | None
    updated_at: int | None
    tags: tuple[str, ...]
    markdown: str
    availability: str
    source: str
    html: str | None
    raw_json: dict[str, Any] | None
    canonical_url: str | None
    imported_url: str | None


def _cookie_jar_from_json(value: Any) -> http.cookiejar.CookieJar:
    values = value.get("cookies") if isinstance(value, dict) else value
    if not isinstance(values, list):
        raise ValueError("cookie JSON must be a list or an object containing a cookies list")
    jar = http.cookiejar.CookieJar()
    for item in values:
        if not isinstance(item, dict) or not item.get("name") or "value" not in item:
            raise ValueError("each JSON cookie must contain name and value")
        domain = str(item.get("domain") or ".medium.com")
        path = str(item.get("path") or "/")
        jar.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=str(item["name"]),
                value=str(item["value"]),
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=bool(domain),
                domain_initial_dot=domain.startswith("."),
                path=path,
                path_specified=True,
                secure=bool(item.get("secure", True)),
                expires=int(item["expires"]) if item.get("expires") else None,
                discard=not bool(item.get("expires")),
                comment=None,
                comment_url=None,
                rest={"HttpOnly": item.get("httpOnly", False)},
                rfc2109=False,
            )
        )
    return jar


def load_cookie_file(path: Path) -> http.cookiejar.CookieJar:
    """Load Netscape cookies.txt or Playwright-style JSON without logging values."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not read cookie file: {exc}") from exc
    if not raw.strip():
        raise ValueError("cookie file is empty")
    if raw.lstrip().startswith(("[", "{")):
        try:
            return _cookie_jar_from_json(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid cookie JSON: {exc}") from exc
    jar = http.cookiejar.MozillaCookieJar(str(path))
    try:
        jar.load(ignore_discard=True, ignore_expires=False)
    except (OSError, http.cookiejar.LoadError) as exc:
        raise ValueError(f"invalid Netscape cookie file: {exc}") from exc
    return jar


def load_browser_cookies(browser: str) -> http.cookiejar.CookieJar:
    """Import an existing local browser's Medium cookies."""
    function = getattr(browser_cookie3, browser, None)
    if function is None or not callable(function):
        raise ValueError(f"unsupported browser for cookie import: {browser}")
    try:
        return function(domain_name=".medium.com")
    except browser_cookie3.BrowserCookieError as exc:
        raise ValueError(f"could not import {browser} cookies: {exc}") from exc


def _structured_payload(content: bytes) -> dict[str, Any]:
    if len(content) > MAX_JSON_BYTES:
        raise DirectError(f"Medium JSON response exceeded {MAX_JSON_BYTES} bytes")
    text = content.decode("utf-8", errors="replace")
    if text.startswith(XSSI_PREFIX):
        text = text[len(XSSI_PREFIX) :]
    else:
        start = text.find("{")
        if start < 0:
            raise DirectError("Medium JSON response did not contain an object")
        text = text[start:]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DirectError(f"invalid Medium JSON response: {exc}") from exc
    payload = value.get("payload") if isinstance(value, dict) else None
    if not isinstance(payload, dict) or not isinstance(payload.get("value"), dict):
        raise DirectError("Medium JSON response did not contain a story payload")
    return payload


def _markups(text: str, markups: list[dict[str, Any]]) -> str:
    """Apply non-overlapping Medium links and emphasis to a paragraph."""
    replacements: list[tuple[int, int, str, str]] = []
    for markup in markups:
        try:
            start = int(markup["start"])
            end = start + int(markup["length"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = markup.get("type")
        if not 0 <= start < end <= len(text):
            continue
        if kind == 3 and markup.get("href"):
            replacements.append((start, end, "[", f"]({markup['href']})"))
        elif kind == 1:
            replacements.append((start, end, "**", "**"))
        elif kind == 2:
            replacements.append((start, end, "*", "*"))
    for start, end, prefix, suffix in sorted(replacements, reverse=True):
        text = text[:start] + prefix + text[start:end] + suffix + text[end:]
    return text


def body_model_to_markdown(body_model: dict[str, Any]) -> str:
    """Render Medium paragraph objects into readable, conservative Markdown."""
    sections: list[str] = []
    for index, paragraph in enumerate(body_model.get("paragraphs") or []):
        if not isinstance(paragraph, dict):
            continue
        text = str(paragraph.get("text") or "").strip()
        kind = paragraph.get("type")
        rendered = _markups(text, paragraph.get("markups") or [])
        if kind == 3:
            sections.append(f"# {rendered}" if index == 0 else f"## {rendered}")
        elif kind == 13:
            sections.append(f"## {rendered}")
        elif kind in {6, 7}:
            sections.append("\n".join(f"> {line}" for line in rendered.splitlines()))
        elif kind == 8:
            sections.append(f"```\n{text}\n```")
        elif kind == 10:
            sections.append(f"- {rendered}")
        elif kind == 4:
            if rendered:
                sections.append(f"*[Image: {rendered}]*")
        elif rendered:
            sections.append(rendered)
    return "\n\n".join(sections).strip() + "\n" if sections else ""


def _author(payload: dict[str, Any], creator_id: str | None) -> tuple[str | None, str | None]:
    users = (payload.get("references") or {}).get("User") or {}
    creator = users.get(creator_id) or {}
    return creator.get("name"), creator.get("username")


def _tags(value: dict[str, Any]) -> tuple[str, ...]:
    raw = (value.get("virtuals") or {}).get("tags") or []
    return tuple(
        str(item.get("slug") or item.get("name"))
        for item in raw
        if isinstance(item, dict) and (item.get("slug") or item.get("name"))
    )


class DirectClient:
    """Retrieve Medium pages sequentially using curl-cffi's Chrome impersonation."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        cookie_file: Path | None = None,
        cookies_from_browser: str | None = None,
    ) -> None:
        self.timeout = timeout
        if cookie_file is not None and cookies_from_browser is not None:
            raise ValueError("choose either a cookie file or browser-cookie import, not both")
        if cookie_file is not None:
            cookies = load_cookie_file(cookie_file)
        elif cookies_from_browser is not None:
            cookies = load_browser_cookies(cookies_from_browser)
        else:
            cookies = None
        self.authenticated = cookies is not None
        self._session = requests.Session(impersonate="chrome", cookies=cookies)

    def _get(self, url: str, *, expected: str, maximum: int) -> requests.Response:
        try:
            response = self._session.get(
                url,
                headers={"Accept-Language": "en-GB,en;q=0.9"},
                timeout=self.timeout,
                allow_redirects=True,
            )
        except requests.RequestsError as exc:
            raise DirectError(f"direct request failed: {exc}") from exc
        if response.status_code >= 400:
            raise DirectError(f"direct request returned HTTP {response.status_code}")
        content_type = response.headers.get("Content-Type", "").lower()
        if expected not in content_type:
            raise DirectError(
                f"expected {expected}, received {content_type or 'an unknown content type'}"
            )
        if len(response.content) > maximum:
            raise DirectError(f"direct response exceeded {maximum} bytes")
        return response

    def get_article(self, reference: ArticleReference) -> DirectArticle:
        json_response = self._get(
            f"https://medium.com/p/{reference.article_id}?format=json",
            expected="json",
            maximum=MAX_JSON_BYTES,
        )
        payload = _structured_payload(json_response.content)
        value = payload["value"]
        content = value.get("content") or {}
        body_model = content.get("bodyModel") or {}
        structured_markdown = body_model_to_markdown(body_model)
        locked = bool(content.get("isLockedPreviewOnly"))
        author, username = _author(payload, value.get("creatorId"))

        page_html: str | None = None
        page_markdown = ""
        try:
            page_response = self._get(
                reference.source_url,
                expected="html",
                maximum=MAX_PAGE_BYTES,
            )
            page_html = page_response.text
            soup = BeautifulSoup(page_html, "html.parser")
            if soup.title and "cloudflare" in soup.title.get_text(" ", strip=True).lower():
                raise DirectError("Cloudflare returned an access page", kind="blocked")
            page_markdown = html_to_markdown(page_html, source_url=str(page_response.url))
        except (DirectError, ValueError):
            pass

        markdown = max((structured_markdown, page_markdown), key=len)
        if not markdown:
            raise DirectError("Medium returned metadata but no extractable story content")
        source = "medium_json"
        if page_markdown and len(page_markdown) >= len(structured_markdown):
            source = "medium_page"
        if self.authenticated:
            source += "_with_cookies"
        return DirectArticle(
            article_id=reference.article_id,
            title=str(value.get("title") or "Untitled Medium story"),
            url=str(value.get("mediumUrl") or reference.source_url),
            author=author or value.get("displayAuthor"),
            username=username,
            published_at=value.get("firstPublishedAt"),
            updated_at=value.get("updatedAt"),
            tags=_tags(value),
            markdown=markdown,
            availability="preview" if locked else "full",
            source=source,
            html=page_html,
            raw_json=payload,
            canonical_url=value.get("webCanonicalUrl") or value.get("canonicalUrl"),
            imported_url=value.get("importedUrl") or None,
        )
