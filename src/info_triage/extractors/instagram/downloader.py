"""Download Instagram post metadata, comments, and media.

Retrieval goes through yt-dlp, run out of process by the same self-updating
runner as YouTube. Since 2026-10-30 Instagram refuses Instaloader's anonymous
GraphQL query outright, while yt-dlp — which impersonates a browser's TLS
fingerprint and asks the query Instagram's own logged-out web client asks —
still retrieves the caption, every carousel slide and the visible comments; see
`experiments/instagram-retrieval/`. yt-dlp only describes the post here: the
bytes are fetched by `_download_url`, which resumes into `.part` files and
reuses media already on disk.
"""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from info_triage.extractors.artifacts import (
    METADATA_NAME,
    RAW_DIR,
    STATUS_NAME,
    status_for_reason,
)
from info_triage.extractors.youtube.runner import ManagedYtDlp, YtDlpError

from .prepare import prepare_content

# yt-dlp's wording for each way Instagram declines a logged-out request,
# matched case-insensitively against its error text. Order matters: the
# "empty media response" message also mentions logging in.
RATE_LIMIT_MARKERS = (
    "rate-limit",
    "rate limit",
    "please wait a few minutes",
    "empty media response",
    "not granting access",
    "http error 429",
    "too many requests",
)
LOGIN_MARKERS = (
    "login required",
    "redirected to the login page",
    "registered users",
    "restricted video",
    "use --cookies",
    "http error 401",
)
UNAVAILABLE_MARKERS = (
    "not available",
    "page not found",
    "http error 404",
    "does not exist",
    "has been removed",
)

HASHTAG_RE = re.compile(r"(?<![\w&])#(\w+)")
MENTION_RE = re.compile(r"(?<![\w.])@([\w.]+\w)")


@dataclass(slots=True)
class DownloadOptions:
    output_dir: Path
    max_comments: int = 50
    comment_scan_limit: int = 0
    # Both are an Instagram login and therefore account credentials. Neither is
    # needed for public posts; a login only widens what Instagram returns.
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None


def post_url(shortcode: str) -> str:
    """The canonical post URL; yt-dlp resolves reels and TV posts from it too."""
    return f"https://www.instagram.com/p/{shortcode}/"


def failure_reason(message: str) -> str:
    """Return the stable reason key for one yt-dlp error message."""
    text = message.casefold()
    if any(marker in text for marker in RATE_LIMIT_MARKERS):
        return "rate-limited"
    if any(marker in text for marker in LOGIN_MARKERS):
        return "login-required"
    if any(marker in text for marker in UNAVAILABLE_MARKERS):
        return "post-unavailable"
    return "retrieval-error"


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(_json_value(value), ensure_ascii=False, indent=2) + "\n")


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _timestamp(value: Any) -> str | None:
    seconds = _integer(value) if not isinstance(value, float) else int(value)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def fetch_post(runner: ManagedYtDlp, shortcode: str, options: DownloadOptions) -> dict[str, Any]:
    """Ask yt-dlp to describe one post, without downloading anything.

    `--ignore-no-formats-error` keeps image slides: yt-dlp is a video tool and
    otherwise drops every slide that has no video, which for a carousel of
    photos is all of them. Their images are in each entry's `thumbnails`.
    """
    arguments = ["-J", "--ignore-no-formats-error", "--write-comments", "--no-warnings"]
    if options.cookies_file:
        arguments += ["--cookies", str(options.cookies_file)]
    if options.cookies_from_browser:
        arguments += ["--cookies-from-browser", options.cookies_from_browser]
    return runner.run_json([*arguments, "--", post_url(shortcode)])


def _entries(info: dict[str, Any]) -> list[dict[str, Any] | None]:
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        return [entry if isinstance(entry, dict) else None for entry in entries]
    return [info]


def _image_url(entry: dict[str, Any]) -> str | None:
    # yt-dlp lists Instagram's image candidates reversed, so the full-size
    # image Instagram puts first is the last thumbnail.
    thumbnails = [item for item in entry.get("thumbnails") or [] if item.get("url")]
    return thumbnails[-1]["url"] if thumbnails else None


def _video_url(entry: dict[str, Any]) -> str | None:
    """Instagram's own progressive mp4 first; it carries the audio track.

    The DASH renditions are video-only, so one is taken only when a slide offers
    nothing else — on-screen text is still worth reading without the sound.
    """
    formats = [item for item in entry.get("formats") or [] if item.get("url")]
    progressive = [
        item for item in formats if not str(item.get("format_id", "")).startswith("dash-")
    ]
    if progressive:
        return progressive[0]["url"]
    return formats[-1]["url"] if formats else None


def _media_items(info: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for index, entry in enumerate(_entries(info), 1):
        if entry is None:
            items.append({"index": index, "is_video": False, "download_url": None})
            continue
        video = _video_url(entry)
        image = _image_url(entry)
        items.append(
            {
                "index": index,
                "is_video": video is not None,
                "download_url": video or image,
                "thumbnail_url": image if video else None,
                "duration_seconds": entry.get("duration"),
            }
        )
    return items


def _metadata(info: dict[str, Any], source_url: str, shortcode: str) -> dict[str, Any]:
    """Normalise what yt-dlp reports. A field it does not report is left out."""
    caption = info.get("description") or None
    entries = _entries(info)
    is_carousel = info.get("_type") == "playlist"
    is_video = not is_carousel and bool(info.get("formats"))
    metadata: dict[str, Any] = {
        "source_url": source_url,
        "canonical_url": post_url(shortcode),
        "shortcode": shortcode,
        "type": "carousel" if is_carousel else "video" if is_video else "image",
        "media_count": len(entries),
        "owner": {
            "username": info.get("channel") or "",
            "id": _integer(info.get("uploader_id")),
            "name": info.get("uploader") or None,
        },
        "created_at_utc": _timestamp(info.get("timestamp")),
        "caption": caption,
        "hashtags": HASHTAG_RE.findall(caption or ""),
        "mentions": MENTION_RE.findall(caption or ""),
        "likes_count": _integer(info.get("like_count")),
        "comments_count": _integer(info.get("comment_count")),
        "is_video": is_video,
        "video_duration_seconds": info.get("duration") if is_video else None,
    }
    return {key: value for key, value in metadata.items() if value is not None}


def _comment_dict(comment: dict[str, Any]) -> dict[str, Any]:
    parent = comment.get("parent")
    return {
        "id": _integer(comment.get("id")),
        "parent_id": _integer(parent) if parent not in (None, "root") else None,
        "owner_username": str(comment.get("author") or ""),
        "owner_id": _integer(comment.get("author_id")),
        "text": str(comment.get("text") or ""),
        "created_at_utc": _timestamp(comment.get("timestamp")) or "",
        "likes_count": _integer(comment.get("like_count")) or 0,
    }


def _read_comments(
    info: dict[str, Any], scan_limit: int = 0
) -> tuple[list[dict[str, Any]] | None, bool]:
    """Return the post's comments, or None when yt-dlp returned no comment list.

    A carousel repeats the post's comment list on every slide; it is read once.
    """
    raw = info.get("comments")
    if raw is None:
        raw = next(
            (entry.get("comments") for entry in _entries(info) if entry and entry.get("comments")),
            None,
        )
    if raw is None:
        return None, False
    comments = [_comment_dict(item) for item in raw if isinstance(item, dict) and item.get("text")]
    truncated = bool(scan_limit) and len(comments) > scan_limit
    return (comments[:scan_limit] if truncated else comments), truncated


def _select_comments(comments: list[dict[str, Any]], owner: str, maximum: int) -> dict[str, Any]:
    by_time = sorted(comments, key=lambda item: item["created_at_utc"])
    owner_comments = [
        item for item in by_time if item["owner_username"].casefold() == owner.casefold()
    ]
    popular = sorted(comments, key=lambda item: (-item["likes_count"], item["created_at_utc"]))[
        :maximum
    ]
    return {
        "submitter_first_comment": owner_comments[0] if owner_comments else None,
        "selected_count": len(popular),
        "selected_order": "likes_count descending, then created_at_utc ascending",
        "comments": popular,
    }


def _extension(url: str, content_type: str | None, is_video: bool) -> str:
    if content_type:
        extension = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if extension:
            return ".jpg" if extension == ".jpe" else extension
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}:
        return suffix
    return ".mp4" if is_video else ".jpg"


def _download_url(session: requests.Session, url: str, base_path: Path, is_video: bool) -> Path:
    response = session.get(url, stream=True, timeout=120)
    response.raise_for_status()
    output = base_path.with_suffix(_extension(url, response.headers.get("Content-Type"), is_video))
    temporary = output.with_suffix(output.suffix + ".part")
    with temporary.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    temporary.replace(output)
    return output


def _write_comments(post_dir: Path, info: dict[str, Any], owner: str, options: DownloadOptions):
    comments, truncated = _read_comments(info, options.comment_scan_limit)
    if comments is None:
        _write_json(
            post_dir / RAW_DIR / "comments.json",
            {"comments": [], "submitter_first_comment": None},
        )
        return "unavailable"
    selection = _select_comments(comments, owner, options.max_comments)
    selection.update({"scanned_count": len(comments), "scan_truncated": truncated})
    _write_json(post_dir / RAW_DIR / "comments.json", selection)
    text_sections: list[str] = []
    if selection["submitter_first_comment"]:
        text_sections.append(
            "SUBMITTER FIRST COMMENT\n" + selection["submitter_first_comment"]["text"]
        )
    if selection["comments"]:
        rendered = [
            f"[{item['likes_count']} likes] @{item['owner_username']}: {item['text']}"
            for item in selection["comments"]
        ]
        text_sections.append("POPULAR COMMENTS\n" + "\n\n".join(rendered))
    _write_text(
        post_dir / RAW_DIR / "comments.txt",
        "\n\n".join(text_sections) + ("\n" if text_sections else ""),
    )
    return "complete"


def download_post(
    runner: ManagedYtDlp,
    source_url: str,
    shortcode: str,
    options: DownloadOptions,
    *,
    session: requests.Session | None = None,
) -> Path:
    post_dir = options.output_dir / shortcode
    media_dir = post_dir / RAW_DIR / "media"
    ocr_dir = post_dir / RAW_DIR / "ocr"
    media_dir.mkdir(parents=True, exist_ok=True)
    ocr_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {"shortcode": shortcode, "status": "started", "errors": []}
    _write_json(post_dir / STATUS_NAME, status)

    try:
        info = fetch_post(runner, shortcode, options)
    except (YtDlpError, OSError) as exc:
        reason = failure_reason(str(exc))
        status.update({"status": status_for_reason(reason), "reason": reason})
        status["errors"].append({"stage": "metadata", "error": str(exc)})
        _write_json(post_dir / STATUS_NAME, status)
        prepare_content(post_dir)
        return post_dir

    _write_json(post_dir / RAW_DIR / "metadata_raw.json", info)
    metadata = _metadata(info, source_url, shortcode)
    media = _media_items(info)
    http = session or requests.Session()
    headers = info.get("http_headers")
    if isinstance(headers, dict):
        http.headers.update({str(key): str(value) for key, value in headers.items()})

    downloaded: list[str] = []
    for item in media:
        url = item.pop("download_url", None)
        if not url:
            status["errors"].append(
                {"stage": "media", "error": f"Instagram returned no media for item {item['index']}"}
            )
            continue
        kind = "video" if item["is_video"] else "image"
        base_name = f"{item['index']:02d}_{kind}"
        existing = next(
            (
                candidate
                for candidate in media_dir.glob(f"{base_name}.*")
                if candidate.is_file()
                and not candidate.name.endswith(".part")
                and candidate.stat().st_size > 0
            ),
            None,
        )
        try:
            path = existing or _download_url(http, url, media_dir / base_name, item["is_video"])
        except requests.RequestException as exc:
            status["errors"].append(
                {"stage": "media", "error": f"item {item['index']}: {type(exc).__name__}: {exc}"}
            )
            continue
        item["file"] = str(path.relative_to(post_dir))
        downloaded.append(item["file"])

    metadata["media"] = media
    _write_json(post_dir / METADATA_NAME, metadata)
    caption = metadata.get("caption") or ""
    _write_text(post_dir / RAW_DIR / "caption.txt", caption + ("\n" if caption else ""))
    status["comments"] = _write_comments(post_dir, info, metadata["owner"]["username"], options)

    missing = len(media) - len(downloaded)
    if not caption and not downloaded:
        status.update({"status": "failed", "reason": "post-unavailable"})
    elif missing:
        status.update({"status": "partial", "reason": "media-missing"})
    else:
        status["status"] = "complete"
    status.update({"media_files": downloaded, "owner_username": metadata["owner"]["username"]})
    _write_json(post_dir / STATUS_NAME, status)
    prepare_content(post_dir)
    return post_dir
