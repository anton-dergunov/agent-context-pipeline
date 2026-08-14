"""Render retrieved YouTube artifacts into the uniform extraction shape."""

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


def prepare_content(video_dir: Path) -> None:
    """Write content.md and comments.md from what the extractor retrieved."""
    metadata = _read_json(video_dir / "metadata.json", {})
    comments = _read_json(video_dir / RAW_DIR / "comments.json", {"comments": []})
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

    if comments.get("comments"):
        rendered = ["## Top comments", ""]
        for parent in comments["comments"]:
            rendered.append(
                f"**{parent.get('author', '')}** ({parent.get('like_count', 0)} likes) — "
                f"{parent.get('text', '')}\n"
            )
            rendered.extend(
                f"  - **{reply.get('author', '')}** ({reply.get('like_count', 0)} likes) — "
                f"{reply.get('text', '')}"
                for reply in parent.get("replies", [])
            )
        (video_dir / COMMENTS_NAME).write_text(
            "\n".join(rendered).rstrip() + "\n", encoding="utf-8"
        )


def _duration(seconds: Any) -> str:
    if not isinstance(seconds, int | float) or seconds <= 0:
        return ""
    minutes, remainder = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{remainder:02d}" if hours else f"{minutes}:{remainder:02d}"
