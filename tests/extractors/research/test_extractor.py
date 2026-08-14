from __future__ import annotations

import json

from info_triage.extractors.document.models import ExtractionError, FetchResult
from info_triage.extractors.research.extractor import (
    ResearchExtractor,
    ResearchOptions,
)

METADATA = b"""<html><head>
<meta name="citation_title" content="A Useful Paper">
<meta name="citation_author" content="Ada Author">
<meta name="citation_author" content="Bob Writer">
<meta name="citation_abstract" content="A compact abstract.">
<meta name="citation_publication_date" content="2025/07">
<meta name="citation_doi" content="10.1234/example">
</head><body><article><div id="abstract">Abstract: The full visible abstract.</div>
<p>Publication page.</p></article></body></html>"""


class Client:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        value = self.mapping[url]
        if isinstance(value, Exception):
            raise value
        return value


def _html(url, body=METADATA):
    return FetchResult(url, url, "text/html", "html", body, 200)


def test_arxiv_html_body_is_preferred_and_metadata_is_prepended(tmp_path):
    metadata_url = "https://arxiv.org/abs/2010.00747"
    body_url = "https://arxiv.org/html/2010.00747"
    client = Client(
        {
            metadata_url: _html(metadata_url),
            body_url: _html(
                body_url,
                b"<html><body><article><h1>A Useful Paper</h1><p>Full paper body.</p></article></body></html>",
            ),
        }
    )
    path, complete = ResearchExtractor(ResearchOptions(output_dir=tmp_path), client=client).extract(
        metadata_url
    )
    assert complete
    paper = (path / "content.md").read_text()
    assert paper.startswith("# A Useful Paper")
    assert "## Abstract" in paper
    assert "## Full paper" in paper
    assert "Full paper body" in paper
    assert client.calls == [metadata_url, body_url]


def test_arxiv_versioned_pdf_candidate_overrides_unversioned_metadata_link(tmp_path):
    metadata_url = "https://arxiv.org/abs/1709.05584v3"
    html_url = "https://arxiv.org/html/1709.05584v3"
    pdf_url = "https://arxiv.org/pdf/1709.05584v3"
    metadata = METADATA.replace(
        b"</head>",
        b'<meta name="citation_pdf_url" content="https://arxiv.org/pdf/1709.05584"></head>',
    )
    client = Client(
        {
            metadata_url: _html(metadata_url, metadata),
            html_url: ExtractionError("missing", reason="http-error"),
            pdf_url: ExtractionError(
                "conversion omitted in this routing test", reason="http-error"
            ),
        }
    )
    path, complete = ResearchExtractor(ResearchOptions(output_dir=tmp_path), client=client).extract(
        "https://arxiv.org/abs/1709.05584v3.pdf"
    )
    assert not complete
    assert client.calls == [metadata_url, html_url, pdf_url]
    assert json.loads((path / "metadata.json").read_text())["canonical_url"].endswith(
        "1709.05584v3"
    )


def test_metadata_success_retains_partial_markdown_when_body_fails(tmp_path):
    page = "https://aclanthology.org/2025.acl-long.461/"
    pdf = "https://aclanthology.org/2025.acl-long.461.pdf"
    client = Client({page: _html(page), pdf: ExtractionError("HTTP 404", reason="http-error")})
    path, complete = ResearchExtractor(
        ResearchOptions(output_dir=tmp_path, keep_raw=False), client=client
    ).extract(pdf)
    assert not complete
    assert "A compact abstract" in (path / "content.md").read_text()
    status = json.loads((path / "status.json").read_text())
    assert status["status"] == "partial"
    assert status["reason"] == "paper-body-unavailable"
    assert not (path / "raw" / "metadata-source.html").exists()


def test_openreview_uses_v1_after_v2_failure(tmp_path):
    paper_id = "BZ5a1r-kVsf"
    v2 = f"https://api2.openreview.net/notes?id={paper_id}"
    v1 = f"https://api.openreview.net/notes?id={paper_id}"
    pdf = f"https://openreview.net/pdf?id={paper_id}"
    payload = json.dumps(
        {
            "notes": [
                {
                    "content": {
                        "title": "Legacy paper",
                        "authors": ["One Author"],
                        "abstract": "Legacy abstract",
                    }
                }
            ]
        }
    ).encode()
    client = Client(
        {
            v2: ExtractionError("missing", reason="http-error"),
            v1: FetchResult(v1, v1, "application/json", "json", payload, 200),
            pdf: ExtractionError("missing", reason="http-error"),
        }
    )
    path, complete = ResearchExtractor(ResearchOptions(output_dir=tmp_path), client=client).extract(
        f"https://openreview.net/forum?id={paper_id}"
    )
    assert not complete
    assert "Legacy paper" in (path / "content.md").read_text()
    assert client.calls[:2] == [v2, v1]


def test_failed_research_rerun_does_not_mix_old_paper_with_new_status(tmp_path):
    page = "https://aclanthology.org/2025.acl-long.461/"
    pdf = "https://aclanthology.org/2025.acl-long.461.pdf"
    client = Client(
        {
            page: _html(page),
            pdf: ExtractionError("missing", reason="http-error"),
        }
    )
    extractor = ResearchExtractor(ResearchOptions(output_dir=tmp_path), client=client)
    path, _ = extractor.extract(page)
    assert (path / "content.md").exists()

    client.mapping[page] = ExtractionError("blocked", reason="access-blocked")
    _, complete = extractor.extract(page)
    assert not complete
    assert not (path / "content.md").exists()
    assert json.loads((path / "status.json").read_text())["status"] == "failed"
    assert not list(path.parent.glob(f".{path.name}.*"))
