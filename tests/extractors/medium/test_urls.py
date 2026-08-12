import pytest

from info_triage.extractors.medium.urls import parse_article_url, with_feed_url


@pytest.mark.parametrize(
    ("url", "article_id", "feed_url"),
    [
        (
            "https://medium.com/the-leading-indicator/story-dc2e9c207d00?source=test",
            "dc2e9c207d00",
            "https://medium.com/feed/the-leading-indicator",
        ),
        (
            "https://medium.com/@writer/story-abcdef123456",
            "abcdef123456",
            "https://medium.com/feed/@writer",
        ),
        (
            "https://writer.medium.com/story-0123456789ab",
            "0123456789ab",
            "https://writer.medium.com/feed",
        ),
        (
            "https://publication.example/story-fedcba987654",
            "fedcba987654",
            "https://publication.example/feed",
        ),
    ],
)
def test_parse_article_url(url, article_id, feed_url):
    reference = parse_article_url(url)
    assert reference.article_id == article_id
    assert reference.feed_url == feed_url
    assert "?" not in reference.source_url


@pytest.mark.parametrize(
    "url",
    [
        "http://medium.com/publication/story-abcdef123456",
        "https://medium.com/p/abcdef123456",
        "https://medium.com/publication/not-an-id",
        "https://user:secret@medium.com/publication/story-abcdef123456",
    ],
)
def test_rejects_unsupported_urls(url):
    with pytest.raises(ValueError):
        parse_article_url(url)


def test_explicit_author_feed_override():
    reference = parse_article_url("https://medium.com/publication/story-abcdef123456")
    updated = with_feed_url(reference, "https://medium.com/feed/@actual-author?ignored=yes")
    assert updated.feed_url == "https://medium.com/feed/@actual-author"
    assert updated.article_id == reference.article_id
    publication = with_feed_url(reference, "https://medium.com/feed/actual-publication")
    assert publication.feed_url == "https://medium.com/feed/actual-publication"


def test_preserves_friend_link_but_drops_tracking():
    reference = parse_article_url(
        "https://medium.com/example/story-abcdef123456?source=tracking&sk=friend-token"
    )
    assert reference.source_url.endswith("?sk=friend-token")


@pytest.mark.parametrize(
    "url",
    [
        "http://medium.com/feed/@writer",
        "https://user:secret@medium.com/feed/@writer",
        "https://medium.com/not-a-feed",
    ],
)
def test_rejects_unsupported_feed_overrides(url):
    reference = parse_article_url("https://medium.com/publication/story-abcdef123456")
    with pytest.raises(ValueError):
        with_feed_url(reference, url)
