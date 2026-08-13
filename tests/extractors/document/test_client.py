from __future__ import annotations

import pytest

from info_triage.extractors.document.client import DocumentClient
from info_triage.extractors.document.models import ExtractionError


class Response:
    def __init__(self, url, body=b"", content_type="text/html", status=200, headers=None):
        self.url = url
        self.body = body
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}

    def iter_content(self, chunk_size=0):
        yield self.body

    def close(self):
        pass


class Session:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    def get(self, url, **_kwargs):
        self.calls.append(url)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_fetch_sniffs_pdf_magic_bytes(monkeypatch):
    monkeypatch.setattr(
        "info_triage.extractors.document.client.public_url_error", lambda _url: None
    )
    session = Session(
        [Response("https://example.com/download", b"%PDF-1.7\n", "binary/octet-stream")]
    )
    client = DocumentClient(browser_fallback=False)
    client.session = session
    assert client.fetch("https://example.com/download").kind == "pdf"


def test_fetch_revalidates_redirect_destination(monkeypatch):
    def unsafe(url):
        return "private" if url == "http://127.0.0.1/file" else None

    monkeypatch.setattr("info_triage.extractors.document.client.public_url_error", unsafe)
    session = Session(
        [
            Response(
                "https://example.com/start",
                status=302,
                headers={"Location": "http://127.0.0.1/file"},
            )
        ]
    )
    client = DocumentClient(browser_fallback=False)
    client.session = session
    with pytest.raises(ExtractionError) as raised:
        client.fetch("https://example.com/start")
    assert raised.value.reason == "unsafe-url"


def test_browser_compatible_retry_recovers_blocked_page(monkeypatch):
    monkeypatch.setattr(
        "info_triage.extractors.document.client.public_url_error", lambda _url: None
    )
    client = DocumentClient()
    client.session = Session([Response("https://example.com", status=403)])
    client.browser_session = Session(
        [Response("https://example.com", b"<html><body>ok</body></html>")]
    )
    result = client.fetch("https://example.com")
    assert result.method == "browser-compatible"
    assert result.kind == "html"
