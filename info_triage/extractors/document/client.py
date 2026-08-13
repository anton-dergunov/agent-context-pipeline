"""Bounded public HTTP retrieval for extractors."""

from __future__ import annotations

from contextlib import closing
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from curl_cffi import requests as browser_requests

from info_triage.utilities.url_resolution import (
    make_browser_session,
    make_session,
    public_url_error,
)

from .models import ExtractionError, FetchResult

REDIRECTS = {301, 302, 303, 307, 308}
HTML_TYPES = ("text/html", "application/xhtml+xml")
JSON_TYPES = ("application/json", "application/ld+json")


class DocumentClient:
    """Fetch public documents with strict redirect and response bounds."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retries: int = 2,
        max_html_bytes: int = 10 * 1024 * 1024,
        max_pdf_bytes: int = 50 * 1024 * 1024,
        browser_fallback: bool = True,
    ) -> None:
        self.timeout = timeout
        self.max_html_bytes = max_html_bytes
        self.max_pdf_bytes = max_pdf_bytes
        self.session = make_session(retries)
        self.browser_session = make_browser_session() if browser_fallback else None

    @staticmethod
    def _kind(content_type: str, url: str, body: bytes) -> str:
        lowered = content_type.lower()
        prefix = body[:4096].lstrip().lower()
        if "application/pdf" in lowered or body.startswith(b"%PDF-"):
            return "pdf"
        if any(value in lowered for value in JSON_TYPES) or prefix.startswith((b"{", b"[")):
            return "json"
        if any(value in lowered for value in HTML_TYPES) or prefix.startswith(
            (b"<!doctype html", b"<html", b"<head", b"<body")
        ):
            return "html"
        if urlsplit(url).path.lower().endswith(".pdf"):
            return "pdf"
        return "unknown"

    @staticmethod
    def _read(response: Any, limit: int) -> bytes:
        length = response.headers.get("Content-Length")
        if length:
            try:
                if int(length) > limit:
                    raise ExtractionError(
                        f"response exceeded {limit} bytes", reason="response-too-large"
                    )
            except ValueError:
                pass
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > limit:
                raise ExtractionError(
                    f"response exceeded {limit} bytes", reason="response-too-large"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _fetch_once(self, url: str, session: Any, method: str) -> FetchResult:
        current = url
        visited: set[str] = set()
        try:
            for _ in range(10):
                if current in visited:
                    raise ExtractionError(
                        "redirect loop detected", reason="redirect-loop", url=current
                    )
                visited.add(current)
                if error := public_url_error(current):
                    raise ExtractionError(f"unsafe URL: {error}", reason="unsafe-url", url=current)
                with closing(
                    session.get(
                        current,
                        allow_redirects=False,
                        timeout=(min(15.0, self.timeout), self.timeout),
                        stream=True,
                    )
                ) as response:
                    response_url = str(getattr(response, "url", None) or current)
                    if error := public_url_error(response_url):
                        raise ExtractionError(
                            f"unsafe URL: {error}", reason="unsafe-url", url=response_url
                        )
                    status = int(response.status_code)
                    if status in REDIRECTS:
                        location = response.headers.get("Location")
                        if not location:
                            raise ExtractionError(
                                "redirect did not provide a destination",
                                reason="redirect-destination-missing",
                                url=response_url,
                            )
                        current = urljoin(response_url, location)
                        continue
                    if status >= 400:
                        reason = "access-blocked" if status in {401, 403, 429} else "http-error"
                        raise ExtractionError(f"HTTP {status}", reason=reason, url=response_url)

                    content_type = str(response.headers.get("Content-Type", "")).lower()
                    likely_pdf = "application/pdf" in content_type or urlsplit(
                        response_url
                    ).path.lower().endswith(".pdf")
                    limit = self.max_pdf_bytes if likely_pdf else self.max_html_bytes
                    body = self._read(response, limit)
                    kind = self._kind(content_type, response_url, body)
                    final_limit = self.max_pdf_bytes if kind == "pdf" else self.max_html_bytes
                    if len(body) > final_limit:
                        raise ExtractionError(
                            f"response exceeded {final_limit} bytes", reason="response-too-large"
                        )
                    if kind == "unknown":
                        raise ExtractionError(
                            f"unsupported content type: {content_type or 'unknown'}",
                            reason="unsupported-content",
                            url=response_url,
                        )
                    return FetchResult(
                        requested_url=url,
                        final_url=response_url,
                        content_type=content_type,
                        kind=kind,
                        body=body,
                        status_code=status,
                        headers={str(key): str(value) for key, value in response.headers.items()},
                        method=method,
                    )
            raise ExtractionError("redirect limit exceeded", reason="redirect-limit", url=current)
        except ExtractionError:
            raise
        except (requests.RequestException, browser_requests.RequestsError) as exc:
            raise ExtractionError(
                f"request failed: {exc}", reason="request-error", url=current
            ) from exc

    def fetch(self, url: str) -> FetchResult:
        """Fetch a document, retrying access/request failures with Chrome-compatible HTTP."""
        try:
            return self._fetch_once(url, self.session, "http")
        except ExtractionError as first:
            if self.browser_session is None or first.reason not in {
                "access-blocked",
                "http-error",
                "request-error",
            }:
                raise
            try:
                return self._fetch_once(url, self.browser_session, "browser-compatible")
            except ExtractionError as second:
                if second.reason == "unsafe-url":
                    raise
                raise first
