"""Render retrieved Instagram artifacts into the uniform extraction shape."""

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


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def prepare_content(post_dir: Path) -> None:
    """Write content.md from the caption, on-screen text and spoken audio.

    Instagram comments are retrieved and kept on disk for inspection but never
    reach content.md — measured as ~38% of the tokens and none of the signal.
    """
    metadata = _read_json(post_dir / "metadata.json", {})
    owner = metadata.get("owner", {}).get("username", "")

    title = metadata.get("title") or (f"Instagram post by @{owner}" if owner else "Instagram post")
    lines = [f"# {title}", ""]
    facts = [
        ("Owner", f"@{owner}" if owner else ""),
        ("Created", metadata.get("created_at_utc", "")),
        ("URL", metadata.get("source_url", "")),
    ]
    lines.extend(f"- {label}: {value}" for label, value in facts if value)

    for heading, value in (
        ("Caption", metadata.get("caption")),
        ("Accessibility description", metadata.get("accessibility_caption")),
        ("On-screen text", _read_text(post_dir / RAW_DIR / "ocr_text.txt")),
        ("Spoken audio", _read_text(post_dir / RAW_DIR / "transcript.txt")),
    ):
        if value:
            lines.extend(["", f"## {heading}", "", str(value).strip()])

    (post_dir / CONTENT_NAME).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    comments = _read_json(
        post_dir / RAW_DIR / "comments.json", {"comments": [], "submitter_first_comment": None}
    )
    rendered: list[str] = []
    first = comments.get("submitter_first_comment")
    if first:
        rendered.extend(["## Post owner's first comment", "", first.get("text", "").strip(), ""])
    if comments.get("comments"):
        rendered.extend(["## Popular comments", ""])
        rendered.extend(
            f"**@{item.get('owner_username', '')}** ({item.get('likes_count', 0)} likes) — "
            f"{item.get('text', '')}\n"
            for item in comments["comments"]
        )
    if rendered:
        (post_dir / COMMENTS_NAME).write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
