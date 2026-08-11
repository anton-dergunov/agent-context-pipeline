"""Prepare LinkedIn extraction output for downstream processing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def prepare_llm_input(post_dir: Path) -> None:
    metadata = _read_json(post_dir / "metadata.json", {})
    comments = _read_json(post_dir / "comments.json", {"comments": [], "first_comment": None})
    post_text = (
        (post_dir / "post.txt").read_text(encoding="utf-8").strip()
        if (post_dir / "post.txt").exists()
        else ""
    )

    payload = {
        "schema_version": 1,
        "purpose": "Anonymous public LinkedIn post input for downstream information processing",
        "post": metadata,
        "post_text": post_text,
        "first_public_comment": comments.get("first_comment"),
        "public_selected_comments": comments.get("comments", []),
        "comment_selection": {
            "selection": comments.get("selection"),
            "reported_count": comments.get("reported_count", 0),
            "returned_count": comments.get("returned_count", 0),
            "is_complete": False,
        },
    }
    (post_dir / "llm_input.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sections = [
        "LINKEDIN PUBLIC POST",
        f"URL: {metadata.get('canonical_url', metadata.get('request_url', ''))}",
        f"Author: {metadata.get('author', {}).get('name') or ''}",
        f"Published: {metadata.get('published_at') or metadata.get('relative_time') or ''}",
        "",
        "POST",
        post_text,
    ]
    if comments.get("first_comment"):
        first = comments["first_comment"]
        sections.extend(
            [
                "",
                "FIRST PUBLICLY RETURNED COMMENT",
                f"{first.get('author_name') or 'Unknown'}: {first.get('text', '')}",
            ]
        )
    if comments.get("comments"):
        rendered = [
            f"{item.get('author_name') or 'Unknown'}: {item.get('text', '')}"
            for item in comments["comments"]
        ]
        sections.extend(["", "PUBLICLY SELECTED COMMENTS", "\n\n".join(rendered)])
    (post_dir / "llm_input.txt").write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
