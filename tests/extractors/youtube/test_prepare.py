"""Tests for YouTube's harvest and comment policy."""

import json

from info_triage.extractors.artifacts import COMMENTS_NAME, RAW_DIR
from info_triage.extractors.youtube.prepare import harvest_links, prepare_content

DESCRIPTION = """A talk about diffusion policies.

Paper: https://arxiv.org/abs/2410.04840
Code: https://github.com/example/policy.
Follow me: https://twitter.com/example
Merch: https://amzn.to/xyz
Slides: https://example.com/slides.pdf
"""


def video(tmp_path, *, description=DESCRIPTION, comments=()):
    video_dir = tmp_path / "ztdTed5egrM"
    (video_dir / RAW_DIR).mkdir(parents=True)
    (video_dir / "metadata.json").write_text(
        json.dumps({"title": "A talk", "channel": "Example", "description": description}),
        encoding="utf-8",
    )
    (video_dir / RAW_DIR / "comments.json").write_text(
        json.dumps({"comments": list(comments)}), encoding="utf-8"
    )
    return video_dir


def comment(author, text, *, uploader=False, replies=()):
    return {
        "author": author,
        "author_is_uploader": uploader,
        "text": text,
        "like_count": 0,
        "replies": list(replies),
    }


def test_description_links_are_harvested_in_order_and_stripped_of_punctuation(tmp_path):
    harvested = harvest_links(video(tmp_path))

    assert [link.url for link in harvested] == [
        "https://arxiv.org/abs/2410.04840",
        # The sentence-ending period is not part of the URL.
        "https://github.com/example/policy",
        "https://twitter.com/example",
        "https://amzn.to/xyz",
        "https://example.com/slides.pdf",
    ]
    assert {link.via for link in harvested} == {"description"}


def test_a_video_without_a_description_harvests_nothing(tmp_path):
    assert harvest_links(video(tmp_path, description="")) == []


def test_uploader_comments_lead_and_every_comment_is_still_kept(tmp_path):
    comments = [
        comment("Viewer", "First!"),
        comment(
            "Example",
            "Correction: the benchmark is v2.",
            uploader=True,
            replies=[comment("Viewer", "Thanks")],
        ),
        comment("Other", "Nice", replies=[comment("Example", "Cheers", uploader=True)]),
    ]

    prepare_content(video(tmp_path, comments=comments))

    rendered = (tmp_path / "ztdTed5egrM" / COMMENTS_NAME).read_text(encoding="utf-8")
    assert rendered.index("## Uploader comments") < rendered.index("## Top comments")
    lead = rendered.split("## Top comments")[0]
    assert "Correction: the benchmark is v2." in lead
    assert "Cheers" in lead
    assert "First!" not in lead
    assert "First!" in rendered


def test_comments_md_is_not_written_when_there_are_none(tmp_path):
    video_dir = video(tmp_path)

    prepare_content(video_dir)

    assert not (video_dir / COMMENTS_NAME).exists()
