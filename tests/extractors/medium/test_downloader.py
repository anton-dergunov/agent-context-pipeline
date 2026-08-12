import json

from info_triage.extractors.medium.downloader import DownloadOptions, download_article
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
        _Client((_article(),)), reference, DownloadOptions(output_dir=tmp_path)
    )
    assert ok
    assert (article_dir / "article.md").read_text() == (
        "# Example story\n\nArticle content for extraction.\n"
    )
    metadata = json.loads((article_dir / "metadata.json").read_text())
    assert metadata["availability"] == "full"
    assert metadata["extraction_source"] == "medium_rss"
    assert json.loads((article_dir / "status.json").read_text())["download"] == "complete"


def test_download_article_reports_rolling_feed_miss(tmp_path):
    reference = parse_article_url("https://medium.com/example/old-story-abcdef123456")
    article_dir, ok = download_article(_Client(()), reference, DownloadOptions(output_dir=tmp_path))
    assert not ok
    status = json.loads((article_dir / "status.json").read_text())
    assert status["download"] == "not_found_in_feed"
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
