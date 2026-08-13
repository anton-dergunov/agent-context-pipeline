from __future__ import annotations

import json

from info_triage.extractors.document.extractor import (
    DocumentExtractor,
    DocumentOptions,
    item_id,
)
from info_triage.extractors.document.models import ExtractionError, FetchResult


class Client:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def fetch(self, _url):
        if self.error:
            raise self.error
        return self.result


def test_html_artifacts_and_raw_toggle(tmp_path):
    result = FetchResult(
        "https://example.com/article",
        "https://example.com/article",
        "text/html",
        "html",
        b"<html><head><title>Example</title></head><body><article><p>Readable article text.</p></article></body></html>",
        200,
    )
    path, complete = DocumentExtractor(
        DocumentOptions(output_dir=tmp_path, keep_raw=False), client=Client(result)
    ).extract(result.requested_url)
    assert complete
    assert "Readable article text" in (path / "content.md").read_text()
    assert not (path / "source.html").exists()
    assert json.loads((path / "status.json").read_text())["status"] == "complete"


def test_fetch_failure_writes_stable_status(tmp_path):
    path, complete = DocumentExtractor(
        DocumentOptions(output_dir=tmp_path),
        client=Client(error=ExtractionError("private", reason="unsafe-url")),
    ).extract("http://127.0.0.1/private")
    assert not complete
    status = json.loads((path / "status.json").read_text())
    assert status["status"] == "failed"
    assert status["reason"] == "unsafe-url"


def test_failed_rerun_atomically_replaces_complete_artifacts(tmp_path):
    url = "https://example.com/article"
    result = FetchResult(
        url,
        url,
        "text/html",
        "html",
        b"<html><body><article><p>Readable article text.</p></article></body></html>",
        200,
    )
    client = Client(result=result)
    extractor = DocumentExtractor(DocumentOptions(output_dir=tmp_path), client=client)
    path, complete = extractor.extract(url)
    assert complete and (path / "content.md").exists()

    client.error = ExtractionError("gone", reason="http-error")
    _, complete = extractor.extract(url)
    assert not complete
    assert not (path / "content.md").exists()
    assert json.loads((path / "status.json").read_text())["status"] == "failed"
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_item_identity_is_deterministic_and_query_order_independent():
    first = item_id("https://EXAMPLE.com/path?a=1&b=2#section")
    second = item_id("https://example.com/path?b=2&a=1")
    assert first == second
