import pytest

from info_triage.extractors.medium.feed import FeedError, MediumFeedClient, parse_feed

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">
  <channel>
    <title>Example - Medium</title><generator>Medium</generator>
    <item>
      <title>Full story</title>
      <link>https://medium.com/example/full-story-abcdef123456?source=rss</link>
      <guid>https://medium.com/p/abcdef123456</guid>
      <category>python</category><category>testing</category>
      <dc:creator>Example Author</dc:creator>
      <pubDate>Tue, 04 Aug 2026 18:01:01 GMT</pubDate>
      <atom:updated>2026-08-04T18:01:01.569Z</atom:updated>
      <content:encoded><![CDATA[<p>First paragraph.</p><h3>Section</h3><p>Second paragraph.</p>]]></content:encoded>
    </item>
    <item>
      <title>Member story</title>
      <link>https://medium.com/example/member-story-fedcba987654?source=rss</link>
      <guid>https://medium.com/p/fedcba987654</guid>
      <description><![CDATA[<div class="medium-feed-item"><p class="medium-feed-snippet">One sentence preview.</p><p class="medium-feed-link"><a href="https://medium.com/example/member-story-fedcba987654">Continue reading</a></p></div>]]></description>
    </item>
  </channel>
</rss>"""


def test_parse_feed_distinguishes_full_articles_and_previews():
    full, preview = parse_feed(RSS)
    assert full.article_id == "abcdef123456"
    assert full.availability == "full"
    assert full.author == "Example Author"
    assert full.categories == ("python", "testing")
    assert preview.article_id == "fedcba987654"
    assert preview.availability == "preview"
    assert "One sentence preview" in preview.html


def test_rejects_non_medium_and_entity_feeds():
    with pytest.raises(FeedError, match="not a Medium"):
        parse_feed(b"<rss><channel><generator>Someone else</generator></channel></rss>")
    with pytest.raises(FeedError, match="unsupported XML"):
        parse_feed(b'<!DOCTYPE rss [<!ENTITY x "value">]><rss/>')


class _Response:
    status_code = 200
    headers = {"Content-Type": "application/rss+xml; charset=UTF-8"}
    content = RSS

    def raise_for_status(self):
        return None

    def close(self):
        return None


def test_client_is_anonymous_and_caches_each_feed():
    requests = []

    def requester(url, **kwargs):
        requests.append((url, kwargs))
        return _Response()

    client = MediumFeedClient(requester=requester)
    first = client.get_articles("https://medium.com/feed/example")
    second = client.get_articles("https://medium.com/feed/example")
    assert first == second
    assert len(requests) == 1
    assert requests[0][1]["cookies"] == {}
    assert "Authorization" not in requests[0][1]["headers"]
