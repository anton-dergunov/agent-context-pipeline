"""Tests for Instagram comment mapping and selection."""

from info_triage.extractors.instagram.downloader import _read_comments, _select_comments


def test_comments_are_ranked_and_owner_first_is_preserved():
    comments = [
        {
            "id": 1,
            "owner_username": "reader",
            "text": "popular",
            "created_at_utc": "2025-01-02",
            "likes_count": 30,
        },
        {
            "id": 2,
            "owner_username": "author",
            "text": "later owner note",
            "created_at_utc": "2025-01-03",
            "likes_count": 2,
        },
        {
            "id": 3,
            "owner_username": "AUTHOR",
            "text": "first owner note",
            "created_at_utc": "2025-01-01",
            "likes_count": 1,
        },
    ]
    selected = _select_comments(comments, "author", 2)
    assert selected["submitter_first_comment"]["id"] == 3
    assert [item["id"] for item in selected["comments"]] == [1, 2]


def _comment(comment_id, parent="root"):
    return {
        "id": str(comment_id),
        "parent": parent,
        "author": f"user{comment_id}",
        "author_id": str(comment_id + 100),
        "text": f"comment {comment_id}",
        "timestamp": 1735689600 + comment_id,
        "like_count": comment_id,
    }


def test_yt_dlp_comments_map_onto_the_stored_shape():
    info = {"comments": [_comment(1), _comment(2, parent="1")]}
    comments, truncated = _read_comments(info, 0)
    assert not truncated
    assert comments[0] == {
        "id": 1,
        "parent_id": None,
        "owner_username": "user1",
        "owner_id": 101,
        "text": "comment 1",
        "created_at_utc": "2025-01-01T00:00:01+00:00",
        "likes_count": 1,
    }
    assert comments[1]["parent_id"] == 1


def test_a_carousel_reads_the_comment_list_its_slides_repeat_once():
    shared = [_comment(1), _comment(2)]
    info = {"_type": "playlist", "entries": [{"comments": shared}, {"comments": shared}]}
    comments, _ = _read_comments(info, 0)
    assert [item["id"] for item in comments] == [1, 2]


def test_positive_comment_scan_limit_is_respected():
    comments, truncated = _read_comments({"comments": [_comment(1), _comment(2), _comment(3)]}, 2)
    assert [item["id"] for item in comments] == [1, 2]
    assert truncated


def test_no_comment_list_is_unavailable_rather_than_empty():
    assert _read_comments({"id": "x"}, 0) == (None, False)
