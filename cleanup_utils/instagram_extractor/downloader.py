from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import browser_cookie3
import instaloader
import requests

from .prepare import prepare_llm_input


@dataclass(slots=True)
class DownloadOptions:
    output_dir: Path
    max_comments: int = 50
    comment_scan_limit: int = 0
    session_file: Path | None = None
    instagram_user: str | None = None
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "_asdict"):
        return {key: _json_value(item) for key, item in value._asdict().items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_json_value(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _browser_cookies(browser: str):
    browser = browser.lower().replace("-", "_")
    function = getattr(browser_cookie3, browser, None)
    if function is None:
        supported = "arc, brave, chrome, chromium, edge, firefox, opera, safari, vivaldi"
        raise ValueError(f"unsupported browser {browser!r}; choose one of: {supported}")
    return function(domain_name="instagram.com")


def make_loader(options: DownloadOptions) -> instaloader.Instaloader:
    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=False,
    )
    if options.session_file and options.session_file.exists():
        if not options.instagram_user:
            raise ValueError("--instagram-user is required with --session-file")
        loader.load_session_from_file(options.instagram_user, str(options.session_file))
    elif options.cookies_file or options.cookies_from_browser:
        if options.cookies_file:
            jar = MozillaCookieJar(str(options.cookies_file))
            jar.load(ignore_discard=True, ignore_expires=True)
        else:
            jar = _browser_cookies(options.cookies_from_browser or "")
        loader.context._session.cookies.update(jar)
        username = loader.test_login()
        if not username:
            raise RuntimeError("the supplied browser cookies do not contain a valid Instagram login")
        if options.instagram_user and username.casefold() != options.instagram_user.casefold():
            raise RuntimeError(f"cookies belong to @{username}, not @{options.instagram_user}")
        # Updating the requests cookie jar is enough for test_login(), but it
        # does not set InstaloaderContext.username.  Instaloader uses that
        # attribute as its is_logged_in flag and otherwise refuses to request
        # comments even though the cookies themselves are valid.  Reloading
        # the cookie data through Instaloader establishes a complete session
        # and also installs the matching CSRF header.
        loader.load_session(username, requests.utils.dict_from_cookiejar(jar))
        options.instagram_user = username
        if options.session_file:
            options.session_file.parent.mkdir(parents=True, exist_ok=True)
            loader.save_session_to_file(str(options.session_file))
    elif options.session_file:
        raise FileNotFoundError(f"session file does not exist: {options.session_file}")
    return loader


def _extension(url: str, content_type: str | None, is_video: bool) -> str:
    if content_type:
        extension = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if extension:
            return ".jpg" if extension == ".jpe" else extension
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}:
        return suffix
    return ".mp4" if is_video else ".jpg"


def _download_url(loader: instaloader.Instaloader, url: str, base_path: Path, is_video: bool) -> Path:
    response = loader.context._session.get(url, stream=True, timeout=120)
    response.raise_for_status()
    output = base_path.with_suffix(_extension(url, response.headers.get("Content-Type"), is_video))
    temporary = output.with_suffix(output.suffix + ".part")
    with temporary.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    temporary.replace(output)
    return output


def _media_items(post: instaloader.Post) -> list[dict[str, Any]]:
    if post.typename == "GraphSidecar":
        nodes = list(post.get_sidecar_nodes())
        return [
            {
                "index": index,
                "is_video": node.is_video,
                "download_url": node.video_url if node.is_video else node.display_url,
                "thumbnail_url": node.display_url if node.is_video else None,
            }
            for index, node in enumerate(nodes, 1)
        ]
    return [
        {
            "index": 1,
            "is_video": post.is_video,
            "download_url": post.video_url if post.is_video else post.url,
            "thumbnail_url": post.url if post.is_video else None,
        }
    ]


def _comment_dict(comment: Any, parent_id: int | None = None) -> dict[str, Any]:
    return {
        "id": int(comment.id),
        "parent_id": parent_id,
        "owner_username": comment.owner.username,
        "owner_id": int(comment.owner.userid),
        "text": comment.text,
        "created_at_utc": comment.created_at_utc.isoformat(),
        "likes_count": int(comment.likes_count),
    }


def _read_comments(post: instaloader.Post, scan_limit: int = 0) -> tuple[list[dict[str, Any]], bool]:
    comments: list[dict[str, Any]] = []
    truncated = False
    for top_level in post.get_comments():
        comments.append(_comment_dict(top_level))
        if scan_limit and len(comments) >= scan_limit:
            truncated = True
            break
        for answer in top_level.answers:
            comments.append(_comment_dict(answer, int(top_level.id)))
            if scan_limit and len(comments) >= scan_limit:
                truncated = True
                break
        if truncated:
            break
    return comments, truncated


def _select_comments(comments: list[dict[str, Any]], owner: str, maximum: int) -> dict[str, Any]:
    by_time = sorted(comments, key=lambda item: item["created_at_utc"])
    owner_comments = [item for item in by_time if item["owner_username"].casefold() == owner.casefold()]
    popular = sorted(comments, key=lambda item: (-item["likes_count"], item["created_at_utc"]))[:maximum]
    return {
        "submitter_first_comment": owner_comments[0] if owner_comments else None,
        "selected_count": len(popular),
        "selected_order": "likes_count descending, then created_at_utc ascending",
        "comments": popular,
    }


def _location(post: instaloader.Post) -> Any:
    try:
        return _json_value(post.location) if post.location else None
    except (KeyError, TypeError, ValueError):
        # Some authenticated iPhone-shaped responses contain a useful
        # location object without the legacy numeric `id` that Instaloader's
        # Post.location property requires. Preserve the available fields.
        node = getattr(post, "_node", {})
        return _json_value(node.get("location"))


def _metadata(post: instaloader.Post, source_url: str) -> dict[str, Any]:
    return {
        "source_url": source_url,
        "canonical_url": f"https://www.instagram.com/p/{post.shortcode}/",
        "shortcode": post.shortcode,
        "media_id": int(post.mediaid),
        "type": post.typename,
        "media_count": int(post.mediacount),
        "owner": {"username": post.owner_username, "id": int(post.owner_id)},
        "created_at_utc": post.date_utc.isoformat(),
        "caption": post.caption,
        "accessibility_caption": post.accessibility_caption,
        "title": post.title,
        "hashtags": list(post.caption_hashtags),
        "mentions": list(post.caption_mentions),
        "tagged_users": list(post.tagged_users),
        "likes_count": int(post.likes),
        "comments_count": int(post.comments),
        "is_video": bool(post.is_video),
        "video_duration_seconds": post.video_duration,
        "video_play_count": post.video_play_count,
        "video_view_count": post.video_view_count,
        "location": _location(post),
    }


def download_post(loader: instaloader.Instaloader, source_url: str, shortcode: str, options: DownloadOptions) -> Path:
    post_dir = options.output_dir / shortcode
    media_dir = post_dir / "media"
    ocr_dir = post_dir / "ocr"
    media_dir.mkdir(parents=True, exist_ok=True)
    ocr_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {"shortcode": shortcode, "download": "started", "errors": []}
    _write_json(post_dir / "status.json", status)

    try:
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        metadata = _metadata(post, source_url)
        media = _media_items(post)
        downloaded: list[str] = []
        for item in media:
            if not item["download_url"]:
                raise RuntimeError(f"Instagram returned no media URL for item {item['index']}")
            kind = "video" if item["is_video"] else "image"
            base_name = f"{item['index']:02d}_{kind}"
            existing = next(
                (
                    candidate
                    for candidate in media_dir.glob(f"{base_name}.*")
                    if candidate.is_file() and not candidate.name.endswith(".part") and candidate.stat().st_size > 0
                ),
                None,
            )
            path = existing or _download_url(
                loader, item["download_url"], media_dir / base_name, item["is_video"]
            )
            item["file"] = str(path.relative_to(post_dir))
            item.pop("download_url", None)
            downloaded.append(item["file"])
        metadata["media"] = media
        _write_json(post_dir / "metadata.json", metadata)
        (post_dir / "caption.txt").write_text((post.caption or "") + ("\n" if post.caption else ""), encoding="utf-8")

        try:
            comments, truncated = _read_comments(post, options.comment_scan_limit)
            selection = _select_comments(comments, post.owner_username, options.max_comments)
            selection.update({"scanned_count": len(comments), "scan_truncated": truncated})
            _write_json(post_dir / "comments.json", selection)
            text_sections: list[str] = []
            if selection["submitter_first_comment"]:
                text_sections.append("SUBMITTER FIRST COMMENT\n" + selection["submitter_first_comment"]["text"])
            if selection["comments"]:
                rendered = [
                    f"[{item['likes_count']} likes] @{item['owner_username']}: {item['text']}"
                    for item in selection["comments"]
                ]
                text_sections.append("POPULAR COMMENTS\n" + "\n\n".join(rendered))
            (post_dir / "comments.txt").write_text("\n\n".join(text_sections) + ("\n" if text_sections else ""), encoding="utf-8")
            status["comments"] = "complete"
        except Exception as exc:
            status["comments"] = "failed"
            status["errors"].append({"stage": "comments", "error": f"{type(exc).__name__}: {exc}"})
            _write_json(post_dir / "comments.json", {"error": str(exc), "comments": [], "submitter_first_comment": None})

        # Keep the source GraphQL node for auditability and future fields. Do
        # not force Instaloader's lazy iPhone endpoint here: Instagram requires
        # login for it even when the public media itself downloaded correctly.
        raw = {"graphql_node": getattr(post, "_node", None)}
        _write_json(post_dir / "metadata_raw.json", raw)
        status.update({"download": "complete", "media_files": downloaded, "owner_username": post.owner_username})
    except Exception as exc:
        status["download"] = "failed"
        status["errors"].append({"stage": "download", "error": f"{type(exc).__name__}: {exc}"})
    _write_json(post_dir / "status.json", status)
    prepare_llm_input(post_dir)
    return post_dir
