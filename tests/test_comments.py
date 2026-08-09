from datetime import UTC, datetime
from types import SimpleNamespace

from instagram_extractor.downloader import _location, _read_comments, _select_comments


def test_comments_are_ranked_and_owner_first_is_preserved():
    comments = [
        {"id": 1, "owner_username": "reader", "text": "popular", "created_at_utc": "2025-01-02", "likes_count": 30},
        {"id": 2, "owner_username": "author", "text": "later owner note", "created_at_utc": "2025-01-03", "likes_count": 2},
        {"id": 3, "owner_username": "AUTHOR", "text": "first owner note", "created_at_utc": "2025-01-01", "likes_count": 1},
    ]
    selected = _select_comments(comments, "author", 2)
    assert selected["submitter_first_comment"]["id"] == 3
    assert [item["id"] for item in selected["comments"]] == [1, 2]


def _comment(comment_id, answers=()):
    return SimpleNamespace(
        id=comment_id,
        owner=SimpleNamespace(username=f"user{comment_id}", userid=comment_id + 100),
        text=f"comment {comment_id}",
        created_at_utc=datetime(2025, 1, comment_id, tzinfo=UTC),
        likes_count=comment_id,
        answers=iter(answers),
    )


def test_zero_comment_scan_limit_reads_every_comment_and_reply():
    post = SimpleNamespace(get_comments=lambda: iter([_comment(1, [_comment(2)]), _comment(3)]))
    comments, truncated = _read_comments(post, 0)
    assert [item["id"] for item in comments] == [1, 2, 3]
    assert not truncated


def test_positive_comment_scan_limit_is_respected():
    post = SimpleNamespace(get_comments=lambda: iter([_comment(1, [_comment(2)]), _comment(3)]))
    comments, truncated = _read_comments(post, 2)
    assert [item["id"] for item in comments] == [1, 2]
    assert truncated


def test_location_falls_back_to_raw_node_when_instaloader_requires_missing_id():
    class PostWithIncompleteLocation:
        _node = {"location": {"name": "Wangxian Valley", "lat": 28.44}}

        @property
        def location(self):
            raise KeyError("id")

    assert _location(PostWithIncompleteLocation()) == {
        "name": "Wangxian Valley",
        "lat": 28.44,
    }
