"""Render retrieved LinkedIn artifacts into the uniform extraction shape."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from info_triage.extractors.artifacts import COMMENTS_NAME, CONTENT_NAME, RAW_DIR


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _comment_line(comment: dict[str, Any]) -> str:
    author = comment.get("author_name") or "Unknown"
    return f"**{author}** — {comment.get('text', '')}".rstrip()


def prepare_content(post_dir: Path, post_text: str) -> None:
    """Write content.md and comments.md from what the downloader retrieved."""
    metadata = _read_json(post_dir / "metadata.json", {})
    comments = _read_json(
        post_dir / RAW_DIR / "comments.json", {"comments": [], "first_comment": None}
    )

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
    # repository, so it leads. Session 6 is what actually reads it.
    rendered: list[str] = []
    first = comments.get("first_comment")
    if first:
        rendered.extend(["## First returned comment", "", _comment_line(first)])
    if comments.get("comments"):
        if rendered:
            rendered.append("")
        rendered.append("## Public comments")
        rendered.append("")
        rendered.extend(_comment_line(item) + "\n" for item in comments["comments"])
    if rendered:
        (post_dir / COMMENTS_NAME).write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
