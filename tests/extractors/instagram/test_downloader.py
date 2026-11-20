"""Tests for retrieving an Instagram post through yt-dlp."""

import json

import pytest

from info_triage.extractors.artifacts import METADATA_NAME, RAW_DIR, STATUS_NAME
from info_triage.extractors.instagram.downloader import (
    DownloadOptions,
    download_post,
    failure_reason,
)
from info_triage.extractors.youtube.runner import YtDlpError


class _Runner:
    def __init__(self, result):
        self.result = result
        self.arguments = None

    def run_json(self, arguments):
        self.arguments = arguments
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Response:
    def __init__(self, content_type):
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield b"bytes"


class _Session:
    def __init__(self):
        self.headers = {}
        self.urls = []

    def get(self, url, stream, timeout):
        self.urls.append(url)
        return _Response("video/mp4" if url.endswith(".mp4") else "image/jpeg")


def _image_slide(name):
    # yt-dlp lists Instagram's candidates smallest first.
    return {
        "formats": [],
        "thumbnails": [
            {"id": "0", "url": f"https://cdn.example/{name}-small.jpg"},
            {"id": "1", "url": f"https://cdn.example/{name}-full.jpg"},
        ],
    }


def _video_slide(name):
    return {
        "formats": [
            {"format_id": "0", "url": f"https://cdn.example/{name}-progressive.mp4"},
            {"format_id": "1", "url": f"https://cdn.example/{name}-progressive-low.mp4"},
            {"format_id": "dash-1v", "url": f"https://cdn.example/{name}-dash.mp4"},
        ],
        "thumbnails": [{"id": "0", "url": f"https://cdn.example/{name}-cover.jpg"}],
        "duration": 7.5,
    }


CAROUSEL = {
    "_type": "playlist",
    "id": "ABC123",
    "description": "Five museums #visitargentina with @friend.name",
    "channel": "author",
    "uploader": "Author Display Name",
    "uploader_id": "42",
    "timestamp": 1735689600,
    "like_count": 10,
    "comment_count": 1,
    "http_headers": {"Referer": "https://www.instagram.com/"},
    "entries": [_image_slide("one"), _video_slide("two")],
}


def _download(tmp_path, result, **options):
    runner, session = _Runner(result), _Session()
    post_dir = download_post(
        runner,
        "https://www.instagram.com/p/ABC123/?img_index=1",
        "ABC123",
        DownloadOptions(output_dir=tmp_path, **options),
        session=session,
    )
    status = json.loads((post_dir / STATUS_NAME).read_text())
    return post_dir, status, runner, session


def test_a_carousel_downloads_the_full_image_and_the_progressive_video(tmp_path):
    post_dir, status, runner, session = _download(tmp_path, CAROUSEL)

    assert status["status"] == "complete"
    assert runner.arguments[-1] == "https://www.instagram.com/p/ABC123/"
    assert "--ignore-no-formats-error" in runner.arguments
    assert session.urls == [
        "https://cdn.example/one-full.jpg",
        "https://cdn.example/two-progressive.mp4",
    ]
    assert session.headers["Referer"] == "https://www.instagram.com/"
    assert sorted(path.name for path in (post_dir / RAW_DIR / "media").iterdir()) == [
        "01_image.jpg",
        "02_video.mp4",
    ]


def test_metadata_reports_what_yt_dlp_knows_and_omits_the_rest(tmp_path):
    post_dir, _, _, _ = _download(tmp_path, CAROUSEL)
    metadata = json.loads((post_dir / METADATA_NAME).read_text())

    assert metadata["type"] == "carousel"
    assert metadata["media_count"] == 2
    assert metadata["owner"] == {"username": "author", "id": 42, "name": "Author Display Name"}
    assert metadata["created_at_utc"] == "2025-01-01T00:00:00+00:00"
    assert metadata["hashtags"] == ["visitargentina"]
    assert metadata["mentions"] == ["friend.name"]
    assert metadata["canonical_url"] == "https://www.instagram.com/p/ABC123/"
    for absent in ("title", "channel", "location", "tagged_users", "accessibility_caption"):
        assert absent not in metadata
    assert [item["file"] for item in metadata["media"]] == [
        "raw/media/01_image.jpg",
        "raw/media/02_video.mp4",
    ]


def test_existing_media_is_reused(tmp_path):
    media = tmp_path / "ABC123" / RAW_DIR / "media"
    media.mkdir(parents=True)
    (media / "01_image.jpg").write_bytes(b"already here")
    _, _, _, session = _download(tmp_path, CAROUSEL)
    assert session.urls == ["https://cdn.example/two-progressive.mp4"]


def test_a_slide_yt_dlp_could_not_describe_makes_the_post_partial(tmp_path):
    post_dir, status, _, _ = _download(tmp_path, {**CAROUSEL, "entries": [_image_slide("a"), None]})
    assert (status["status"], status["reason"]) == ("partial", "media-missing")
    assert (post_dir / RAW_DIR / "media" / "01_image.jpg").is_file()


def test_cookies_are_passed_to_yt_dlp_only_when_configured(tmp_path):
    _, _, runner, _ = _download(tmp_path, CAROUSEL)
    assert "--cookies" not in runner.arguments
    _, _, runner, _ = _download(tmp_path, CAROUSEL, cookies_file=tmp_path / "cookies.txt")
    assert runner.arguments[runner.arguments.index("--cookies") + 1] == str(
        tmp_path / "cookies.txt"
    )


@pytest.mark.parametrize(
    ("message", "status", "reason"),
    [
        (
            "ERROR: [Instagram] ABC: Instagram sent an empty media response. Check if this post "
            "is accessible in your browser without being logged-in. If it is not, then use "
            "--cookies-from-browser or --cookies for the authentication.",
            "blocked",
            "rate-limited",
        ),
        (
            "ERROR: The webpage request was redirected to the login page. You have exceeded "
            "the rate-limit for accessing posts anonymously",
            "blocked",
            "rate-limited",
        ),
        (
            "ERROR: This content is only available for registered users who follow this account",
            "blocked",
            "login-required",
        ),
        ("ERROR: HTTP Error 404: Not Found", "failed", "post-unavailable"),
        ("ERROR: something new", "failed", "retrieval-error"),
    ],
)
def test_a_refusal_is_told_apart_from_a_missing_post(tmp_path, message, status, reason):
    _, written, _, _ = _download(tmp_path, YtDlpError(message, kind="unknown"))
    assert (written["status"], written["reason"]) == (status, reason)
    assert failure_reason(message) == reason
    assert written["errors"][0]["stage"] == "metadata"
