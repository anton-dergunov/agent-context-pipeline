"""Render retrieved LinkedIn artifacts into the uniform extraction shape.

This module also owns LinkedIn's comment policy, which is one measured rule: the
paper or the repository lives in the author's own comment, and the rest of the
thread is filler. The same rule decides what leads `comments.md` and what the
pipeline follows for a second round of retrieval, so the two can never disagree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from info_triage.extractors.artifacts import (
    COMMENTS_NAME,
    CONTENT_NAME,
    RAW_DIR,
    HarvestedLink,
)

URL_RE = re.compile(r"https?://[^\s<>\"\]]+", re.IGNORECASE)
LINKEDIN_HOSTS = ("linkedin.com", "lnkd.in")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _comment_line(comment: dict[str, Any]) -> str:
    author = comment.get("author_name") or "Unknown"
    return f"**{author}** — {comment.get('text', '')}".rstrip()


def _normalized_name(value: Any) -> str:
    return " ".join(str(value).split()).casefold() if isinstance(value, str) else ""


def _profile_path(value: Any) -> str:
    """Reduce a profile URL to the part that identifies the person."""
    if not isinstance(value, str) or not value.strip():
        return ""
    return urlsplit(value.strip()).path.rstrip("/").casefold()


def author_comments(
    metadata: dict[str, Any], comments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return the comments the post's own author left, which is where the link is."""
    author = metadata.get("author")
    author = author if isinstance(author, dict) else {}
    profile = _profile_path(author.get("url"))
    name = _normalized_name(author.get("name"))
    if not profile and not name:
        return []
    selected = []
    for comment in comments:
        commenter = _profile_path(comment.get("author_url"))
        if profile and commenter:
            # A profile URL is unambiguous; two people can share a display name.
            if commenter == profile:
                selected.append(comment)
            continue
        if name and _normalized_name(comment.get("author_name")) == name:
            selected.append(comment)
    return selected


def _comment_urls(comment: dict[str, Any]) -> list[str]:
    """Return the URLs in one comment, from the parser's links or from its text."""
    urls = []
    parsed = comment.get("links")
    if isinstance(parsed, list):
        urls = [item.get("url") for item in parsed if isinstance(item, dict)]
    urls = [url for url in urls if isinstance(url, str) and url.strip()]
    return urls or URL_RE.findall(str(comment.get("text") or ""))


def _is_linkedin(url: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    return any(host == known or host.endswith(f".{known}") for known in LINKEDIN_HOSTS)


def first_offsite_comment(comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first comment carrying a link that leaves LinkedIn.

    The fallback for a post whose author never commented: somebody else supplied
    the artifact the post is about.
    """
    for comment in comments:
        if any(not _is_linkedin(url) for url in _comment_urls(comment)):
            return comment
    return None


def harvest_links(post_dir: Path) -> list[HarvestedLink]:
    """Return what this post points at: its body links, then its author's comments."""
    metadata = _read_json(post_dir / "metadata.json", {})
    payload = _read_json(post_dir / RAW_DIR / "comments.json", {})
    comments = payload.get("comments") if isinstance(payload, dict) else None
    comments = [item for item in comments if isinstance(item, dict)] if comments else []

    harvested = [
        HarvestedLink(item["url"], "post body")
        for item in metadata.get("links") or []
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"].strip()
    ]

    own = author_comments(metadata, comments)
    if own:
        harvested.extend(
            HarvestedLink(url, "author comment")
            for comment in own
            for url in _comment_urls(comment)
        )
    else:
        fallback = first_offsite_comment(comments)
        if fallback is not None:
            harvested.extend(HarvestedLink(url, "first comment") for url in _comment_urls(fallback))
    return harvested


def prepare_content(post_dir: Path, post_text: str) -> None:
    """Write content.md and comments.md from what the downloader retrieved."""
    metadata = _read_json(post_dir / "metadata.json", {})
    payload = _read_json(post_dir / RAW_DIR / "comments.json", {})
    comments = payload.get("comments") if isinstance(payload, dict) else None
    comments = [item for item in comments if isinstance(item, dict)] if comments else []

    author = metadata.get("author", {}).get("name") or ""
    published = metadata.get("published_at") or metadata.get("relative_time") or ""
    heading = metadata.get("headline") or (
        f"LinkedIn post by {author}" if author else "LinkedIn post"
    )

    lines = [f"# {heading}", ""]
    facts = [
        ("Author", author),
        ("Published", published),
        ("URL", metadata.get("canonical_url") or metadata.get("request_url") or ""),
    ]
    lines.extend(f"- {label}: {value}" for label, value in facts if value)
    lines.extend(["", post_text.strip()])
    (post_dir / CONTENT_NAME).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    # The author's own comment is where LinkedIn posts put the paper or the
    # repository, so it leads. The rest is kept but is visibly demoted.
    own = author_comments(metadata, comments)
    own_indexes = {id(comment) for comment in own}
    rest = [comment for comment in comments if id(comment) not in own_indexes]
    rendered: list[str] = []
    for title, selection in (("Author comments", own), ("Other comments", rest)):
        if not selection:
            continue
        if rendered:
            rendered.append("")
        rendered.extend([f"## {title}", ""])
        rendered.extend(_comment_line(comment) + "\n" for comment in selection)
    if rendered:
        (post_dir / COMMENTS_NAME).write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
