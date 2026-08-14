"""Tests for LinkedIn's comment policy: the author's comment is the whole point.

Modelled on the measured worked example — a post whose author's own first comment
carries the paper, followed by nine comments of engagement filler.
"""

import json

from info_triage.extractors.artifacts import COMMENTS_NAME, RAW_DIR
from info_triage.extractors.linkedin.prepare import (
    author_comments,
    harvest_links,
    prepare_content,
)

PAPER = "https://arxiv.org/abs/2606.19857"
AUTHOR_URL = "https://www.linkedin.com/in/maxime-labonne"


def comment(index, author, text, *, url=None, links=None):
    value = {
        "index": index,
        "author_name": author,
        "author_url": url,
        "text": text,
        "links": links if links is not None else [],
    }
    return value


def post(tmp_path, comments, *, author_url=AUTHOR_URL, body_links=()):
    post_dir = tmp_path / "7487448227336716288"
    (post_dir / RAW_DIR).mkdir(parents=True)
    (post_dir / "metadata.json").write_text(
        json.dumps(
            {
                "author": {"name": "Maxime Labonne", "url": author_url},
                "headline": "LLMs don't need readable text",
                "canonical_url": "https://www.linkedin.com/posts/activity-7487448227336716288",
                "links": [{"text": t, "url": u} for t, u in body_links],
            }
        ),
        encoding="utf-8",
    )
    (post_dir / RAW_DIR / "comments.json").write_text(
        json.dumps({"comments": comments}), encoding="utf-8"
    )
    return post_dir


def test_the_paper_in_the_authors_own_comment_is_harvested(tmp_path):
    post_dir = post(
        tmp_path,
        [
            comment(
                1,
                "Maxime Labonne",
                f"📝 Paper: {PAPER}",
                url=AUTHOR_URL,
                links=[{"text": "Paper", "url": PAPER}],
            ),
            comment(2, "Someone Else", "Great insights! 🔥", url="https://linkedin.com/in/other"),
            comment(
                3,
                "Another Person",
                "Check my newsletter https://example.com/spam",
                url="https://linkedin.com/in/another",
            ),
        ],
    )

    harvested = harvest_links(post_dir)

    assert [(link.url, link.via) for link in harvested] == [(PAPER, "author comment")]


def test_the_post_body_leads_and_the_author_follows(tmp_path):
    post_dir = post(
        tmp_path,
        [comment(1, "Maxime Labonne", PAPER, url=AUTHOR_URL, links=[{"url": PAPER}])],
        body_links=[("repo", "https://github.com/mlabonne/babeltele")],
    )

    harvested = harvest_links(post_dir)

    assert [(link.url, link.via) for link in harvested] == [
        ("https://github.com/mlabonne/babeltele", "post body"),
        (PAPER, "author comment"),
    ]


def test_a_display_name_identifies_the_author_when_no_profile_url_was_parsed(tmp_path):
    """The JSON-LD comment path carries a name and no profile link."""
    comments = [
        comment(1, "Someone Else", "Nice"),
        comment(2, "  maxime  labonne ", f"Paper: {PAPER}"),
    ]
    post_dir = post(tmp_path, comments, author_url=None)
    metadata = json.loads((post_dir / "metadata.json").read_text(encoding="utf-8"))

    assert [item["index"] for item in author_comments(metadata, comments)] == [2]
    assert [link.url for link in harvest_links(post_dir)] == [PAPER]


def test_the_first_offsite_comment_is_the_fallback_only_when_the_author_is_silent(tmp_path):
    post_dir = post(
        tmp_path,
        [
            comment(1, "Someone Else", "Love this", url="https://linkedin.com/in/other"),
            comment(
                2,
                "Another Person",
                f"The paper is here {PAPER}",
                url="https://linkedin.com/in/another",
                links=[{"url": PAPER}],
            ),
            comment(3, "Third Person", f"Also {PAPER}/v2", url="https://linkedin.com/in/third"),
        ],
    )

    harvested = harvest_links(post_dir)

    assert [(link.url, link.via) for link in harvested] == [(PAPER, "first comment")]


def test_a_comment_linking_only_back_into_linkedin_is_not_the_fallback(tmp_path):
    post_dir = post(
        tmp_path,
        [
            comment(
                1,
                "Someone Else",
                "See https://www.linkedin.com/in/other and https://lnkd.in/abc",
                url="https://linkedin.com/in/other",
            )
        ],
    )

    assert harvest_links(post_dir) == []


def test_comments_md_leads_with_the_author_and_keeps_the_filler(tmp_path):
    post_dir = post(
        tmp_path,
        [
            comment(1, "Fan Account", "🔥🔥🔥", url="https://linkedin.com/in/fan"),
            comment(2, "Maxime Labonne", f"📝 Paper: {PAPER}", url=AUTHOR_URL),
        ],
    )

    prepare_content(post_dir, "The post body.")

    comments = (post_dir / COMMENTS_NAME).read_text(encoding="utf-8")
    assert comments.index("## Author comments") < comments.index("## Other comments")
    assert comments.index("Maxime Labonne") < comments.index("Fan Account")
    # Nothing is discarded: the filler stays on disk for inspection.
    assert "🔥🔥🔥" in comments
