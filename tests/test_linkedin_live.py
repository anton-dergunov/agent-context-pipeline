import json
import os
import time
from pathlib import Path

import pytest

from linkedin_extractor.client import AnonymousClient
from linkedin_extractor.downloader import DownloadOptions, download_post
from linkedin_extractor.urls import load_inputs


pytestmark = pytest.mark.skipif(
    os.environ.get("LINKEDIN_LIVE") != "1",
    reason="set LINKEDIN_LIVE=1 to exercise public LinkedIn pages",
)


def test_live_dataset(tmp_path):
    references = load_inputs([], Path("dataset/linkedin_urls.txt"))
    expected_images = [1, 1, 2, 1, 1]
    client = AnonymousClient(timeout=30, retries=2)
    options = DownloadOptions(output_dir=tmp_path, max_comments=50, request_delay=1.0)

    for index, (reference, image_count) in enumerate(zip(references, expected_images)):
        if index:
            time.sleep(1.0)
        post_dir, complete = download_post(client, reference, options)
        assert complete, (post_dir / "status.json").read_text(encoding="utf-8")
        metadata = json.loads((post_dir / "metadata.json").read_text(encoding="utf-8"))
        comments = json.loads((post_dir / "comments.json").read_text(encoding="utf-8"))
        assert len(metadata["text"]) >= 100
        assert len(metadata["media"]) == image_count
        assert all(item.get("file") for item in metadata["media"])
        assert comments["returned_count"] >= 1
        assert comments["first_comment"]["text"]
        preserved_links = [item["url"] for item in metadata["links"]]
        preserved_links.extend(
            link["url"]
            for comment in comments["comments"]
            for link in comment.get("links", [])
        )
        assert preserved_links
