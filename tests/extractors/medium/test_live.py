import os
from pathlib import Path

import pytest

from info_triage.extractors.medium.direct import DirectClient
from info_triage.extractors.medium.feed import MediumFeedClient
from info_triage.extractors.medium.urls import parse_article_url


@pytest.mark.skipif(os.environ.get("MEDIUM_LIVE") != "1", reason="set MEDIUM_LIVE=1")
def test_supplied_urls_against_documented_rss_feeds():
    fixture = Path(__file__).parents[2] / "fixtures" / "medium_urls.txt"
    references = [
        parse_article_url(line)
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    client = MediumFeedClient()
    found = {
        reference.article_id: next(
            (
                article.availability
                for article in client.get_articles(reference.feed_url)
                if article.article_id == reference.article_id
            ),
            "not_found_in_feed",
        )
        for reference in references
    }
    assert found["dc2e9c207d00"] == "full"
    assert found["f067d56bed54"] == "full"


@pytest.mark.skipif(os.environ.get("MEDIUM_LIVE") != "1", reason="set MEDIUM_LIVE=1")
def test_supplied_urls_have_direct_content():
    fixture = Path(__file__).parents[2] / "fixtures" / "medium_urls.txt"
    references = [
        parse_article_url(line)
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    client = DirectClient()
    articles = {reference.article_id: client.get_article(reference) for reference in references}
    for article_id in ("dc54b0db5d04", "2e2cf411d4f9", "1ec4b5bcec35"):
        assert articles[article_id].availability == "preview"
        assert len(articles[article_id].markdown.split()) >= 200
    for article_id in ("dc2e9c207d00", "f067d56bed54"):
        assert articles[article_id].availability == "full"
        assert len(articles[article_id].markdown.split()) >= 1_000
