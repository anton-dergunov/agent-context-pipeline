from __future__ import annotations

import pytest

from info_triage.extractors.router import route_url


@pytest.mark.parametrize(
    ("url", "handler"),
    [
        ("https://www.instagram.com/p/ABC_def-12/", "instagram"),
        (
            "https://www.linkedin.com/posts/name-topic-share-1234567890123456789-a1b2/",
            "linkedin",
        ),
        ("https://youtu.be/WZxbzEpdjlU", "youtube"),
        ("https://arxiv.org/pdf/2003.00911", "research"),
        ("https://medium.com/example/story-abcdef123456", "medium"),
        ("https://example.com/article", "document"),
    ],
)
def test_route_precedence(url, handler):
    assert route_url(url, resolve_redirectors=False).handler == handler


def test_redirect_destination_is_routed_once():
    result = route_url(
        "https://lnkd.in/paper",
        redirect_resolver=lambda _url: "https://arxiv.org/abs/2003.00911",
    )
    assert result.handler == "research"
    assert result.source_url == "https://lnkd.in/paper"
    assert result.routed_url == "https://arxiv.org/abs/2003.00911"


def test_rejects_non_http_generic_input():
    with pytest.raises(ValueError):
        route_url("file:///tmp/paper.pdf", resolve_redirectors=False)
