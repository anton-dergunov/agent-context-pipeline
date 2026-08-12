import json

from info_triage.extractors.medium.downloader import (
    DownloadOptions,
    ExtractionMethod,
    download_article,
    parse_methods,
)
from info_triage.extractors.medium.feed import FeedArticle
from info_triage.extractors.medium.urls import parse_article_url


class _Client:
    def __init__(self, articles):
        self.articles = articles

    def get_articles(self, _feed_url):
        return self.articles


class _DirectClient:
    def __init__(self, article):
        self.article = article

    def get_article(self, _reference):
        return self.article


class _UnexpectedDirectClient:
    def get_article(self, _reference):
        raise AssertionError("browser method should not run after RSS succeeds")


def _article(availability="full"):
    return FeedArticle(
        article_id="abcdef123456",
        title="Example story",
        url="https://medium.com/example/example-story-abcdef123456",
        author="Author",
        published_at="Tue, 04 Aug 2026 18:01:01 GMT",
        updated_at="2026-08-04T18:01:01.569Z",
        categories=("testing",),
        html="<p>Article content for extraction.</p>",
        availability=availability,
    )


def test_download_article_writes_markdown_and_availability(tmp_path):
    reference = parse_article_url(
        "https://medium.com/example/example-story-abcdef123456?source=tracking"
    )
    article_dir, ok = download_article(
        _Client((_article(),)),
        reference,
        DownloadOptions(output_dir=tmp_path),
        direct_client=_UnexpectedDirectClient(),
    )
    assert ok
    assert (article_dir / "article.md").read_text() == (
        "# Example story\n\nArticle content for extraction.\n"
    )
    metadata = json.loads((article_dir / "metadata.json").read_text())
    assert metadata["availability"] == "full"
    assert metadata["extraction_method"] == "rss"
    assert metadata["extraction_source"] == "medium_rss"
    assert json.loads((article_dir / "status.json").read_text())["download"] == "complete"


def test_download_article_reports_rolling_feed_miss(tmp_path):
    reference = parse_article_url("https://medium.com/example/old-story-abcdef123456")
    article_dir, ok = download_article(_Client(()), reference, DownloadOptions(output_dir=tmp_path))
    assert not ok
    status = json.loads((article_dir / "status.json").read_text())
    assert status["download"] == "unavailable"
    assert status["attempts"] == [
        {"method": "rss", "result": "not_found", "feed_items_checked": 0},
        {"method": "browser", "result": "unavailable", "error": "client not configured"},
    ]
    assert not (article_dir / "article.md").exists()


def test_direct_preview_is_persisted_without_consulting_rss(tmp_path):
    from info_triage.extractors.medium.direct import DirectArticle

    reference = parse_article_url("https://medium.com/example/story-abcdef123456")
    direct = DirectArticle(
        article_id="abcdef123456",
        title="Preview title",
        url=reference.source_url,
        author="Author",
        username="author",
        published_at=1,
        updated_at=2,
        tags=("testing",),
        markdown="# Preview title\n\nSeveral opening paragraphs.\n",
        availability="preview",
        source="medium_page",
        html="<article><p>Several opening paragraphs.</p></article>",
        raw_json={"value": {"content": "preview"}},
        canonical_url=reference.source_url,
        imported_url=None,
    )
    article_dir, ok = download_article(
        _Client(()),
        reference,
        DownloadOptions(output_dir=tmp_path),
        direct_client=_DirectClient(direct),
    )
    assert ok
    assert "Several opening paragraphs" in (article_dir / "article.md").read_text()
    status = json.loads((article_dir / "status.json").read_text())
    assert status["download"] == "preview"
    assert status["markdown_words"] == 6
    assert (article_dir / "metadata_raw.json").exists()


def test_browser_can_be_configured_before_rss(tmp_path):
    from info_triage.extractors.medium.direct import DirectArticle

    reference = parse_article_url("https://medium.com/example/story-abcdef123456")
    direct = DirectArticle(
        article_id="abcdef123456",
        title="Browser result",
        url=reference.source_url,
        author=None,
        username=None,
        published_at=None,
        updated_at=None,
        tags=(),
        markdown="# Browser result\n\nBrowser content.\n",
        availability="preview",
        source="medium_json",
        html=None,
        raw_json=None,
        canonical_url=reference.source_url,
        imported_url=None,
    )
    article_dir, ok = download_article(
        _Client((_article(),)),
        reference,
        DownloadOptions(output_dir=tmp_path, methods=(ExtractionMethod.BROWSER,)),
        direct_client=_DirectClient(direct),
    )
    assert ok
    status = json.loads((article_dir / "status.json").read_text())
    assert status["configured_methods"] == ["browser"]
    assert status["extraction_method"] == "browser"
    assert status["attempts"][0]["method"] == "browser"


def test_parse_ordered_method_list():
    assert parse_methods("rss,browser") == (
        ExtractionMethod.RSS,
        ExtractionMethod.BROWSER,
    )
    assert parse_methods("browser, rss") == (
        ExtractionMethod.BROWSER,
        ExtractionMethod.RSS,
    )


def test_rejects_empty_duplicate_and_unknown_method_lists():
    import pytest

    for value in ("", "rss,rss", "rss,unknown"):
        with pytest.raises(ValueError):
            parse_methods(value)
