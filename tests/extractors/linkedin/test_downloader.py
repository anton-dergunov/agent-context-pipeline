"""Tests for LinkedIn post download artifacts."""

import json

from info_triage.extractors.linkedin.downloader import DownloadOptions, download_post
from info_triage.extractors.linkedin.urls import parse_post_url

HTML = """
<html><body>
  <article class="main-feed-activity-card" data-activity-urn="urn:li:activity:9001"
    data-attributed-urn="urn:li:share:12345">
    <div data-test-id="main-feed-activity-card__entity-lockup">
      <a data-tracking-control-name="public_post_feed-actor-name" href="https://linkedin.com/in/author">Author</a>
    </div>
    <p data-test-id="main-feed-activity-card__commentary">Post body https://lnkd.in/keep</p>
    <ul data-test-id="feed-images-content">
      <li><img data-delayed-url="https://media.licdn.com/image.jpg" alt="image"></li>
    </ul>
    <span data-test-id="social-actions__comments" data-num-comments="1"></span>
    <section class="comment"><a class="comment__author">Reader</a><p class="comment__text">Useful</p></section>
  </article>
</body></html>
"""


class _ImageResponse:
    status_code = 200
    headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"jpeg-data"

    def close(self):
        return None


class _Client:
    def __init__(self):
        self.media_requests = []

    def get_html(self, url):
        return HTML, url

    def get(self, url, **_kwargs):
        self.media_requests.append(url)
        return _ImageResponse()


def test_outputs_and_idempotent_media_download(tmp_path):
    reference = parse_post_url("https://www.linkedin.com/posts/person_example-share-12345-abcd/")
    options = DownloadOptions(output_dir=tmp_path)
    client = _Client()

    post_dir, complete = download_post(client, reference, options)
    assert complete
    assert (
        (post_dir / "post.txt")
        .read_text(encoding="utf-8")
        .endswith("LINKS\nhttps://lnkd.in/keep\n")
    )
    assert (post_dir / "media/01_image.jpg").read_bytes() == b"jpeg-data"
    comments = json.loads((post_dir / "comments.json").read_text(encoding="utf-8"))
    assert comments["is_complete"] is False
    assert comments["first_comment"]["text"] == "Useful"
    metadata = json.loads((post_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["media"][0]["file"] == "media/01_image.jpg"
    assert json.loads((post_dir / "status.json").read_text())["download"] == "complete"

    _, complete = download_post(client, reference, options)
    assert complete
    assert client.media_requests == ["https://media.licdn.com/image.jpg"]
    assert all("lnkd.in" not in url for url in client.media_requests)


def test_parser_failure_retains_response_html(tmp_path):
    class BrokenClient(_Client):
        def get_html(self, url):
            return "<html><body>changed</body></html>", url

    reference = parse_post_url("https://www.linkedin.com/posts/person_example-share-12345-abcd/")
    post_dir, complete = download_post(
        BrokenClient(), reference, DownloadOptions(output_dir=tmp_path)
    )
    assert not complete
    assert (post_dir / "response.html").exists()
    status = json.loads((post_dir / "status.json").read_text())
    assert status["download"] == "failed"
    assert status["errors"][0]["stage"] == "parse"
