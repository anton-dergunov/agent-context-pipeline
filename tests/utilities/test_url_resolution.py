"""Tests for deterministic and network-assisted URL resolution."""

from urllib.parse import quote

import requests

from info_triage.utilities import url_resolution
from info_triage.utilities.url_resolution import (
    Resolution,
    URLResolver,
    embedded_destination,
    replace_urls,
    resolve_url,
)


def _response(url, body=b"", content_type="text/html; charset=utf-8"):
    response = requests.Response()
    response.status_code = 200
    response.url = url
    response.headers["Content-Type"] = content_type
    response._content = body
    response._content_consumed = True
    response.encoding = "utf-8"
    return response


def test_embedded_redirectors_are_decoded_without_network_access():
    destination = "https://example.com/a path?q=1"
    assert embedded_destination(f"https://href.li/?{quote(destination)}") == destination
    assert (
        embedded_destination("https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Farticle")
        == "https://example.com/article"
    )


def test_replace_urls_preserves_prose_punctuation():
    class Resolver:
        def resolve(self, _url):
            return "https://example.com/final"

    assert replace_urls("Read https://bit.ly/item).", Resolver()) == (
        "Read https://example.com/final)."
    )


def test_url_resolver_reuses_successful_cache(monkeypatch):
    calls = []

    def fake_resolve(url, _session, **_kwargs):
        calls.append(url)
        return Resolution("https://example.com/final", True)

    monkeypatch.setattr(url_resolution, "resolve_url", fake_resolve)
    resolver = URLResolver(timeout=1, retries=0, max_html_bytes=100)

    assert resolver.resolve("https://bit.ly/item") == "https://example.com/final"
    assert resolver.resolve("https://bit.ly/item") == "https://example.com/final"
    assert calls == ["https://bit.ly/item"]


def test_failed_resolution_is_recorded_and_partial_target_is_returned(monkeypatch):
    monkeypatch.setattr(
        url_resolution,
        "resolve_url",
        lambda *_args, **_kwargs: Resolution(
            "https://example.com/partial",
            False,
            "timed out",
        ),
    )
    resolver = URLResolver(timeout=1, retries=0, max_html_bytes=100)

    assert resolver.resolve("https://bit.ly/item") == "https://example.com/partial"
    assert resolver.failures == [("https://bit.ly/item", "timed out")]


def test_meta_refresh_and_literal_javascript_redirects_are_followed():
    class Session:
        def __init__(self, responses):
            self.responses = responses

        def get(self, url, **_kwargs):
            return self.responses[url]

    meta = _response(
        "https://bit.ly/item",
        b'<meta http-equiv="refresh" content="0; url=https://short.example/next">',
    )
    javascript = _response(
        "https://short.example/next",
        b'<script>window.location.replace("https://example.com/final")</script>',
    )
    final = _response("https://example.com/final", b"<p>done</p>")
    session = Session(
        {
            "https://bit.ly/item": meta,
            "https://short.example/next": javascript,
            "https://example.com/final": final,
        }
    )

    result = resolve_url(
        "https://bit.ly/item",
        session,
        timeout=1,
        max_html_bytes=1024,
    )

    assert result == Resolution("https://example.com/final", True)


def test_page_level_redirect_loop_is_rejected():
    class Session:
        def get(self, url, **_kwargs):
            target = "https://short.example/b" if url.endswith("/a") else "https://bit.ly/a"
            return _response(
                url,
                f'<meta http-equiv="refresh" content="0; url={target}">'.encode(),
            )

    result = resolve_url(
        "https://bit.ly/a",
        Session(),
        timeout=1,
        max_html_bytes=1024,
    )

    assert not result.succeeded
    assert result.error == "redirect loop detected"


def test_response_read_is_bounded():
    response = _response("https://example.com", b"0123456789")
    assert url_resolution._read_response_body(response, 4) == b"0123"
