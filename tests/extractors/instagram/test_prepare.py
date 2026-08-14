"""Tests for rendering Instagram artifacts into the uniform extraction shape."""

import json

from info_triage.extractors.artifacts import COMMENTS_NAME, CONTENT_NAME, RAW_DIR
from info_triage.extractors.instagram.prepare import prepare_content


def _post(tmp_path):
    post_dir = tmp_path / "post"
    (post_dir / RAW_DIR).mkdir(parents=True)
    (post_dir / "metadata.json").write_text(
        json.dumps(
            {
                "shortcode": "abc",
                "caption": "Caption",
                "source_url": "https://www.instagram.com/reel/abc/",
                "owner": {"username": "someone"},
            }
        ),
        encoding="utf-8",
    )
    return post_dir


def test_content_carries_caption_on_screen_text_and_spoken_audio(tmp_path):
    post_dir = _post(tmp_path)
    (post_dir / RAW_DIR / "transcript.txt").write_text("Hola мир\n", encoding="utf-8")
    (post_dir / RAW_DIR / "ocr_text.txt").write_text("ON SCREEN\n", encoding="utf-8")

    prepare_content(post_dir)

    content = (post_dir / CONTENT_NAME).read_text(encoding="utf-8")
    assert "- Owner: @someone" in content
    assert "## Caption\n\nCaption" in content
    assert "## On-screen text\n\nON SCREEN" in content
    assert "## Spoken audio\n\nHola мир" in content


def test_comments_are_written_separately_and_never_reach_content(tmp_path):
    post_dir = _post(tmp_path)
    (post_dir / RAW_DIR / "comments.json").write_text(
        json.dumps(
            {
                "submitter_first_comment": {"text": "Owner note"},
                "comments": [{"owner_username": "fan", "likes_count": 3, "text": "Nice"}],
            }
        ),
        encoding="utf-8",
    )

    prepare_content(post_dir)

    content = (post_dir / CONTENT_NAME).read_text(encoding="utf-8")
    assert "Owner note" not in content
    assert "Nice" not in content
    comments = (post_dir / COMMENTS_NAME).read_text(encoding="utf-8")
    assert "Owner note" in comments
    assert "**@fan** (3 likes) — Nice" in comments


def test_no_comments_file_when_there_are_no_comments(tmp_path):
    post_dir = _post(tmp_path)

    prepare_content(post_dir)

    assert not (post_dir / COMMENTS_NAME).exists()
