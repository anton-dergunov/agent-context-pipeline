"""Opt-in live audit for the complete URL-title corpus."""

import json
import os
from pathlib import Path

import pytest

from info_triage.utilities.url_resolution import URLResolver, serialize_report

pytestmark = pytest.mark.skipif(
    os.environ.get("URL_TITLES_LIVE") != "1",
    reason="set URL_TITLES_LIVE=1 to audit public URL titles",
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "url_titles.txt"


def test_complete_url_title_corpus_is_processed():
    urls = [
        line
        for raw_line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    ]
    assert len(urls) == 529
    assert len(set(urls)) == 529

    resolver = URLResolver(
        timeout=15,
        retries=0,
        max_html_bytes=2 * 1024 * 1024,
        max_pdf_bytes=20 * 1024 * 1024,
    )
    for url in urls:
        resolver.resolve_link(url)

    report = json.loads(serialize_report(resolver.link_results))
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    assert report["summary"]["total"] == 529
    assert len(report["results"]) == 529
