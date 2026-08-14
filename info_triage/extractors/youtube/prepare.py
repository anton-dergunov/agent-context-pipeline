"""Render retrieved YouTube artifacts into the uniform extraction shape.

This module also owns YouTube's comment and harvest policy: technical channels
put the paper and the repository in the description, and the uploader's own
replies are the only part of a comment thread that carries anything further.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from info_triage.extractors.artifacts import (
    COMMENTS_NAME,
    CONTENT_NAME,
    RAW_DIR,
    HarvestedLink,
)

URL_RE = re.compile(r"https?://[^\s<>\"\])]+", re.IGNORECASE)
# Description URLs commonly end in sentence punctuation that is not part of them.
TRAILING_PUNCTUATION = ".,;:!?'\"”’"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _comments(video_dir: Path) -> list[dict[str, Any]]:
    payload = _read_json(video_dir / RAW_DIR / "comments.json", {})
    comments = payload.get("comments") if isinstance(payload, dict) else None
    return [item for item in comments if isinstance(item, dict)] if comments else []


def author_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the comments the uploader left, replies included."""
    selected = []
    for parent in comments:
        if parent.get("author_is_uploader"):
            selected.append(parent)
        selected.extend(
            reply
            for reply in parent.get("replies") or []
            if isinstance(reply, dict) and reply.get("author_is_uploader")
        )
    return selected


def harvest_links(video_dir: Path) -> list[HarvestedLink]:
    """Return the description's links, in the order the uploader listed them."""
    metadata = _read_json(video_dir / "metadata.json", {})
    description = metadata.get("description")
    if not isinstance(description, str):
        return []
    return [
        HarvestedLink(url.rstrip(TRAILING_PUNCTUATION), "description")
        for url in URL_RE.findall(description)
    ]


def _rendered_comment(comment: dict[str, Any], *, indent: str = "") -> str:
    return (
        f"{indent}**{comment.get('author', '')}** "
        f"({comment.get('like_count', 0)} likes) — {comment.get('text', '')}"
    )


def prepare_content(video_dir: Path) -> None:
    """Write content.md and comments.md from what the extractor retrieved."""
    metadata = _read_json(video_dir / "metadata.json", {})
    comments = _comments(video_dir)
    transcript = _read_json(video_dir / RAW_DIR / "transcript.json", {"status": "unavailable"})

    lines = [f"# {metadata.get('title') or 'YouTube video'}", ""]
    facts = [
        ("Channel", metadata.get("channel", "")),
        ("Published", metadata.get("published_at_utc", "")),
        ("Duration", _duration(metadata.get("duration_seconds"))),
        ("URL", metadata.get("canonical_url", "")),
    ]
    lines.extend(f"- {label}: {value}" for label, value in facts if value)

    visual_text = ""
    ocr_path = video_dir / RAW_DIR / "ocr_text.txt"
    if ocr_path.exists():
        visual_text = ocr_path.read_text(encoding="utf-8").strip()

    for heading, value in (
        ("Description", metadata.get("description")),
        (
            f"Transcript ({transcript.get('source', 'unknown')})",
            transcript.get("text") if transcript.get("status") == "complete" else None,
        ),
        ("On-screen text", visual_text),
    ):
        if value:
            lines.extend(["", f"## {heading}", "", str(value).strip()])

    (video_dir / CONTENT_NAME).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if not comments:
        return
    rendered: list[str] = []
    # The uploader's own replies are where a correction or a follow-up link
    # appears; everything else is ranked by YouTube, not by usefulness.
    own = author_comments(comments)
    if own:
        rendered.extend(["## Uploader comments", ""])
        rendered.extend(_rendered_comment(comment) + "\n" for comment in own)
    if rendered:
        rendered.append("")
    rendered.extend(["## Top comments", ""])
    for parent in comments:
        rendered.append(_rendered_comment(parent) + "\n")
        rendered.extend(
            _rendered_comment(reply, indent="  - ")
            for reply in parent.get("replies") or []
            if isinstance(reply, dict)
        )
    (video_dir / COMMENTS_NAME).write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")


def _duration(seconds: Any) -> str:
    if not isinstance(seconds, int | float) or seconds <= 0:
        return ""
    minutes, remainder = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{remainder:02d}" if hours else f"{minutes}:{remainder:02d}"
