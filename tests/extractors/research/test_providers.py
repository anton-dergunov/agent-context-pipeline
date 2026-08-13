from __future__ import annotations

import pytest

from info_triage.extractors.research.providers import (
    match_research_url,
    parse_html_metadata,
)


@pytest.mark.parametrize(
    ("url", "provider", "paper_id"),
    [
        ("https://arxiv.org/abs/2003.00911.pdf", "arxiv", "2003.00911"),
        ("https://arxiv.org/html/2003.00911v2", "arxiv", "2003.00911v2"),
        ("https://aclanthology.org/2025.acl-long.461.pdf", "acl", "2025.acl-long.461"),
        (
            "https://proceedings.mlr.press/v70/shrikumar17a/shrikumar17a.pdf",
            "pmlr",
            "v70-shrikumar17a",
        ),
        ("https://openreview.net/pdf?id=BZ5a1r-kVsf", "openreview", "BZ5a1r-kVsf"),
        (
            "https://openaccess.thecvf.com/content/CVPR2025/html/Author_Title_paper.html",
            "cvf",
            "content-CVPR2025-Author_Title_paper",
        ),
        (
            "https://proceedings.neurips.cc/paper_files/paper/2024/hash/abc-Abstract-Conference.html",
            "neurips",
            "abc",
        ),
        ("https://www.jmlr.org/papers/v25/22-0803.html", "jmlr", "v25-22-0803"),
        (
            "https://www.researchgate.net/publication/404837031_PRISM-X",
            "researchgate",
            "404837031",
        ),
    ],
)
def test_provider_url_normalization(url, provider, paper_id):
    reference = match_research_url(url)
    assert reference is not None
    assert reference.provider == provider
    assert reference.paper_id == paper_id


def test_arxiv_routes_share_canonical_identity():
    references = [
        match_research_url(f"https://arxiv.org/{route}/2010.00747")
        for route in ("abs", "pdf", "html")
    ]
    assert all(reference is not None for reference in references)
    assert {reference.canonical_url for reference in references if reference} == {
        "https://arxiv.org/abs/2010.00747"
    }


def test_neurips_maps_hash_metadata_to_file_pdf():
    reference = match_research_url(
        "https://proceedings.neurips.cc/paper_files/paper/2024/hash/abc-Abstract-Conference.html"
    )
    assert reference is not None
    assert reference.body_candidates[0].url == (
        "https://proceedings.neurips.cc/paper_files/paper/2024/file/abc-Paper-Conference.pdf"
    )


def test_visible_abstract_and_page_range_are_normalized():
    reference = match_research_url("https://aclanthology.org/2025.acl-long.461/")
    assert reference is not None
    metadata = parse_html_metadata(
        reference,
        """<html><head>
        <meta name="citation_title" content="Paper">
        <meta name="citation_firstpage" content="10">
        <meta name="citation_lastpage" content="19">
        </head><body><div class="acl-abstract">Abstract: Actual abstract text.</div></body></html>""",
        reference.canonical_url,
    )
    assert metadata["abstract"] == "Actual abstract text."
    assert metadata["pages"] == "10-19"


def test_abstract_word_is_not_stripped_from_abstraction():
    reference = match_research_url("https://proceedings.mlr.press/v108/abel20a.html")
    assert reference is not None
    metadata = parse_html_metadata(
        reference,
        '<html><head><meta name="citation_title" content="Paper"></head>'
        '<body><div class="abstract">Abstraction improves learning.</div></body></html>',
        reference.canonical_url,
    )
    assert metadata["abstract"] == "Abstraction improves learning."
