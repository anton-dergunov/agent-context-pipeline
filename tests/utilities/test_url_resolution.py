"""Tests for deterministic and network-assisted URL enrichment."""

import io
import json
from pathlib import Path
from urllib.parse import quote

import pytest
import requests
from pypdf import PdfWriter

from info_triage.utilities import url_resolution
from info_triage.utilities.url_resolution import (
    LinkResolution,
    Resolution,
    URLResolver,
    embedded_destination,
    enrich_links,
    iter_link_occurrences,
    load_cache,
    parse_args,
    replace_urls,
    resolve_link,
    resolve_url,
    serialize_cache,
    serialize_report,
)


def _response(
    url,
    body=b"",
    content_type="text/html; charset=utf-8",
    *,
    status=200,
    headers=None,
):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.headers["Content-Type"] = content_type
    response.headers.update(headers or {})
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


def test_meta_refresh_and_literal_javascript_redirects_are_followed(monkeypatch):
    monkeypatch.setattr(url_resolution, "_unsafe_url_message", lambda _url: None)

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


def test_page_level_redirect_loop_is_rejected(monkeypatch):
    monkeypatch.setattr(url_resolution, "_unsafe_url_message", lambda _url: None)

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


def test_markdown_scanner_classifies_links_and_skips_protected_urls():
    text = (
        "Bare https://example.com).\n"
        "[https://same.test](https://same.test) "
        "[Existing title](https://titled.test) <https://auto.test>\n"
        "`https://code.test` ![image](https://image.test)\n"
        "[ref]: https://reference.test\n"
        '<a href="https://html.test">raw</a>\n'
        "```\nhttps://fenced.test\n```\n"
    )

    assert [
        (item.kind, item.url, item.eligible_for_title) for item in iter_link_occurrences(text)
    ] == [
        ("bare", "https://example.com", True),
        ("markdown", "https://same.test", True),
        ("titled", "https://titled.test", False),
        ("autolink", "https://auto.test", True),
        ("image", "https://image.test", False),
        ("reference", "https://reference.test", False),
    ]


def test_enrich_links_rewrites_only_untitled_constructs_and_reuses_results():
    class Resolver:
        def __init__(self):
            self.calls = []

        def resolve_link(self, url):
            self.calls.append(url)
            return LinkResolution(
                "https://final.test/a_(b)",
                "A [useful] _title_",
            )

    resolver = Resolver()
    source = (
        "https://short.test and https://short.test. "
        "[https://short.test](https://short.test) "
        "[Keep](https://short.test)"
    )

    assert enrich_links(source, resolver) == (
        r"[A \[useful\] \_title\_](https://final.test/a_\(b\)) and "
        r"[A \[useful\] \_title\_](https://final.test/a_\(b\)). "
        r"[A \[useful\] \_title\_](https://final.test/a_\(b\)) "
        "[Keep](https://short.test)"
    )
    assert resolver.calls == ["https://short.test"]


def test_titleless_enrichment_keeps_best_bare_url():
    class Resolver:
        def resolve_link(self, _url):
            return LinkResolution(
                "https://final.test/article",
                None,
                "title-not-found",
                "no trustworthy title found",
            )

    assert (
        enrich_links("[https://short.test](https://short.test)", Resolver())
        == "https://final.test/article"
    )


def test_html_title_precedence_and_normalization():
    response = _response("https://example.com")
    body = b"""
        <html><head>
        <title>Document title</title>
        <meta name="twitter:title" content="Twitter title">
        <meta property="og:title" content="  Open &amp;   Graph  ">
        </head><body><h1>Heading title</h1></body></html>
    """

    assert url_resolution._html_title(body, response) == "Open & Graph"


@pytest.mark.parametrize(
    "title",
    ["Before you continue to YouTube", "Just a moment...", "Client Challenge", "Instagram"],
)
def test_challenge_and_generic_service_titles_are_rejected(title):
    response = _response("https://example.com")
    assert url_resolution._html_title(f"<title>{title}</title>".encode(), response) is None


def test_link_resolution_follows_http_redirect_once_and_extracts_title(monkeypatch):
    monkeypatch.setattr(url_resolution, "_unsafe_url_message", lambda _url: None)

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs["allow_redirects"]))
            if url == "https://short.test/item":
                return _response(
                    url,
                    status=302,
                    headers={"Location": "https://final.test/article"},
                )
            return _response(url, b'<meta property="og:title" content="Final title">')

    session = Session()
    result = resolve_link(
        "https://short.test/item",
        session,
        timeout=1,
        max_html_bytes=1024,
        max_pdf_bytes=2048,
    )

    assert result == LinkResolution("https://final.test/article", "Final title")
    assert session.calls == [
        ("https://short.test/item", False),
        ("https://final.test/article", False),
    ]


def test_pdf_metadata_title_is_supported(monkeypatch):
    monkeypatch.setattr(url_resolution, "_unsafe_url_message", lambda _url: None)
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": "PDF document title"})
    writer.write(output)

    class Session:
        def get(self, url, **_kwargs):
            return _response(url, output.getvalue(), "application/pdf")

    result = resolve_link(
        "https://example.com/paper.pdf",
        Session(),
        timeout=1,
        max_html_bytes=128,
        max_pdf_bytes=4096,
    )

    assert result == LinkResolution("https://example.com/paper.pdf", "PDF document title")


def test_expected_link_failures_have_stable_reasons(monkeypatch):
    unsafe_validator = url_resolution._unsafe_url_message
    monkeypatch.setattr(url_resolution, "_unsafe_url_message", lambda _url: None)

    class Session:
        def __init__(self, response):
            self.response = response

        def get(self, _url, **_kwargs):
            return self.response

    too_large = resolve_link(
        "https://example.com",
        Session(_response("https://example.com", b"12345", headers={"Content-Length": "5"})),
        timeout=1,
        max_html_bytes=4,
        max_pdf_bytes=10,
    )
    unsupported = resolve_link(
        "https://example.com/data",
        Session(_response("https://example.com/data", b"data", "application/octet-stream")),
        timeout=1,
        max_html_bytes=10,
        max_pdf_bytes=10,
    )
    http_error = resolve_link(
        "https://example.com/missing",
        Session(_response("https://example.com/missing", status=404)),
        timeout=1,
        max_html_bytes=10,
        max_pdf_bytes=10,
    )

    assert too_large.reason == "response-too-large"
    assert unsupported.reason == "unsupported-content"
    assert http_error.reason == "http-error"
    monkeypatch.setattr(url_resolution, "_unsafe_url_message", unsafe_validator)
    assert (
        resolve_link(
            "http://127.0.0.1/private",
            Session(_response("http://127.0.0.1/private")),
            timeout=1,
            max_html_bytes=10,
            max_pdf_bytes=10,
        ).reason
        == "unsafe-url"
    )


def test_cache_loader_accepts_legacy_and_serializes_version_two(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"https://short.test": "https://final.test"}))

    cache = load_cache(path)
    serialized = json.loads(serialize_cache(cache))

    assert cache == {"https://short.test": "https://final.test"}
    assert serialized == {
        "version": 2,
        "links": {"https://short.test": {"url": "https://final.test"}},
    }


def test_report_separates_titled_and_expected_failures():
    report = json.loads(
        serialize_report(
            {
                "https://one.test": LinkResolution("https://one.test/final", "One"),
                "https://two.test": LinkResolution(
                    "https://two.test",
                    None,
                    "title-not-found",
                    "no trustworthy title found",
                ),
            }
        )
    )

    assert report["summary"] == {
        "total": 2,
        "titled": 1,
        "untitled": 1,
        "outcomes": {"resolved": 1, "title-not-found": 1},
    }


def test_cli_enriches_by_default_and_exposes_compatibility_limits():
    args = parse_args(["input.txt"])
    compatibility = parse_args(["input.txt", "--urls-only", "--all", "--max-pdf-bytes", "1234"])

    assert not args.urls_only
    assert args.max_pdf_bytes == 20 * 1024 * 1024
    assert compatibility.urls_only
    assert compatibility.all
    assert compatibility.max_pdf_bytes == 1234


def test_url_title_fixture_is_a_deduplicated_529_url_corpus():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "url_titles.txt"
    lines = fixture.read_text(encoding="utf-8").splitlines()
    urls = [line for line in lines if line and not line.startswith("#")]

    assert len(urls) == 529
    assert len(set(urls)) == 529
    assert all(list(url_resolution.iter_urls(url)) == [url] for url in urls)
    assert [line for line in lines if line.startswith("# Source:")] == [
        "# Source: youtube_output/*/llm_input.txt",
        "# Source: medium_output/*/article.md",
        "# Source: /Users/anton/projects/archive/text-cleanup/dataset/Unsorted.org",
    ]
