"""Download public LinkedIn post content and media."""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .client import AnonymousClient, FetchError
from .parser import Image, ParsedPost, ParseError, parse_post
from .prepare import prepare_llm_input
from .urls import PostReference

MAX_IMAGE_BYTES = 50 * 1024 * 1024


@dataclass(slots=True)
class DownloadOptions:
    output_dir: Path
    max_comments: int = 50
    request_delay: float = 1.0


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _image_extension(url: str, content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    known = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
    }
    if media_type in known:
        return known[media_type]
    extension = mimetypes.guess_extension(media_type)
    if extension:
        return ".jpg" if extension == ".jpe" else extension
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return suffix
    raise ValueError(f"could not determine an image extension for {media_type!r}")


def _existing_image(media_dir: Path, index: int) -> Path | None:
    for candidate in media_dir.glob(f"{index:02d}_image.*"):
        if (
            candidate.is_file()
            and not candidate.name.endswith(".part")
            and candidate.stat().st_size > 0
        ):
            return candidate
    return None


def _download_image(client: AnonymousClient, image: Image, media_dir: Path, index: int) -> Path:
    existing = _existing_image(media_dir, index)
    if existing is not None:
        return existing

    response = client.get(image.url, accept="image/avif,image/webp,image/*,*/*;q=0.8", stream=True)
    try:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if not content_type.startswith("image/"):
            raise ValueError(
                f"expected image media, received {content_type or 'an unknown content type'}"
            )
        output = media_dir / f"{index:02d}_image{_image_extension(image.url, content_type)}"
        temporary = output.with_suffix(output.suffix + ".part")
        size = 0
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ValueError(f"image exceeded {MAX_IMAGE_BYTES} bytes")
                    handle.write(chunk)
            if size == 0:
                raise ValueError("image response was empty")
            temporary.replace(output)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return output
    finally:
        response.close()


def _post_text(post: ParsedPost) -> str:
    sections = [post.text]
    if post.links:
        sections.extend(["", "LINKS", *[item.url for item in post.links]])
    return "\n".join(sections).rstrip() + "\n"


def _comments_payload(post: ParsedPost) -> dict[str, Any]:
    comments = [item.to_dict() for item in post.comments]
    return {
        "selection": "Incomplete selection exposed by LinkedIn's anonymous public page",
        "reported_count": post.reported_comment_count,
        "returned_count": len(comments),
        "is_complete": False,
        "first_comment": comments[0] if comments else None,
        "comments": comments,
    }


def _comments_text(post: ParsedPost) -> str:
    if not post.comments:
        return ""
    sections = [
        "INCOMPLETE PUBLIC COMMENT SELECTION",
        f"LinkedIn reports {post.reported_comment_count} comment(s); this anonymous page returned {len(post.comments)}.",
        "",
        "FIRST PUBLICLY RETURNED COMMENT",
        f"{post.comments[0].author_name or 'Unknown'}: {post.comments[0].text}",
        "",
        "PUBLICLY SELECTED COMMENTS",
    ]
    for comment in post.comments:
        likes = f" [{comment.likes_count} reactions]" if comment.likes_count is not None else ""
        sections.append(
            f"{comment.index}. {comment.author_name or 'Unknown'}{likes}: {comment.text}"
        )
    return "\n\n".join(sections).rstrip() + "\n"


def _metadata(post: ParsedPost, media: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source_url": post.source_url,
        "request_url": post.request_url,
        "canonical_url": post.final_url,
        "post_id": post.post_id,
        "activity_urn": post.activity_urn,
        "attributed_urn": post.attributed_urn,
        "featured_activity_urn": post.featured_activity_urn,
        "author": {"name": post.author_name, "url": post.author_url},
        "resharer": {
            "name": post.resharer_name,
            "url": post.resharer_url,
            "context": post.reshare_context,
        }
        if post.reshare_context
        else None,
        "published_at": post.published_at,
        "relative_time": post.relative_time,
        "headline": post.headline,
        "text": post.text,
        "content_type": post.content_type,
        "reaction_count": post.reaction_count,
        "reported_comment_count": post.reported_comment_count,
        "public_comment_count": len(post.comments),
        "links": [item.to_dict() for item in post.links],
        "media": media,
        "extraction_sources": post.extraction_sources,
    }


def download_post(
    client: AnonymousClient,
    reference: PostReference,
    options: DownloadOptions,
) -> tuple[Path, bool]:
    post_dir = options.output_dir / reference.post_id
    media_dir = post_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {
        "post_id": reference.post_id,
        "request_url": reference.request_url,
        "download": "started",
        "errors": [],
    }
    _write_json(post_dir / "status.json", status)

    html: str | None = None
    try:
        html, final_url = client.get_html(reference.request_url)
        post = parse_post(html, reference, final_url=final_url, max_comments=options.max_comments)
    except FetchError as exc:
        status["download"] = exc.kind
        status["errors"].append({"stage": "fetch", "error": str(exc)})
        if exc.body:
            (post_dir / "response.html").write_text(exc.body, encoding="utf-8")
        _write_json(post_dir / "status.json", status)
        return post_dir, False
    except (ParseError, ValueError) as exc:
        status["download"] = "failed"
        status["errors"].append({"stage": "parse", "error": f"{type(exc).__name__}: {exc}"})
        if html:
            (post_dir / "response.html").write_text(html, encoding="utf-8")
        _write_json(post_dir / "status.json", status)
        return post_dir, False

    (post_dir / "post.txt").write_text(_post_text(post), encoding="utf-8")
    comments_payload = _comments_payload(post)
    _write_json(post_dir / "comments.json", comments_payload)
    (post_dir / "comments.txt").write_text(_comments_text(post), encoding="utf-8")
    _write_json(
        post_dir / "metadata_raw.json",
        {
            "json_ld": post.raw_json_ld,
            "extraction_sources": post.extraction_sources,
            "target": {
                "activity_urn": post.activity_urn,
                "attributed_urn": post.attributed_urn,
                "featured_activity_urn": post.featured_activity_urn,
            },
        },
    )

    media: list[dict[str, Any]] = []
    for index, image in enumerate(post.images, 1):
        item: dict[str, Any] = image.to_dict()
        try:
            path = _download_image(client, image, media_dir, index)
            item["file"] = str(path.relative_to(post_dir))
        except (FetchError, requests.RequestException, OSError, ValueError) as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
            status["errors"].append({"stage": "media", "index": index, "error": item["error"]})
        media.append(item)

    _write_json(post_dir / "metadata.json", _metadata(post, media))
    prepare_llm_input(post_dir)
    status.update(
        {
            "download": "partial" if status["errors"] else "complete",
            "post_text_chars": len(post.text),
            "media_found": len(post.images),
            "media_downloaded": sum(1 for item in media if item.get("file")),
            "reported_comments": post.reported_comment_count,
            "public_comments_returned": len(post.comments),
        }
    )
    _write_json(post_dir / "status.json", status)
    return post_dir, not status["errors"]
