"""Write compact downstream inputs from standalone YouTube artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def prepare_llm_input(video_dir: Path) -> None:
    metadata = _read_json(video_dir / "metadata.json", {})
    comments = _read_json(video_dir / "comments.json", {"comments": []})
    transcript = _read_json(video_dir / "transcript.json", {"status": "unavailable"})
    ocr = _read_json(video_dir / "ocr/video.ocr.json", {})
    payload = {
        "schema_version": 1,
        "purpose": "Input prepared for later summarization/information extraction",
        "video": metadata,
        "comments": comments.get("comments", []),
        "comment_selection": {
            "parent_count": comments.get("parent_count", 0),
            "reply_count": comments.get("reply_count", 0),
            "error": comments.get("error"),
        },
        "transcript": transcript,
        "visual_text": {
            "source_file": ocr.get("source_file"),
            "engine": ocr.get("engine"),
            "text_segments": ocr.get("llm_ready_segments", []),
        },
    }
    (video_dir / "llm_input.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sections = [
        "YOUTUBE VIDEO",
        f"URL: {metadata.get('canonical_url', '')}",
        f"Video ID: {metadata.get('video_id', video_dir.name)}",
        f"Kind: {metadata.get('kind', '')}",
        f"Channel: {metadata.get('channel', '')}",
        f"Published (UTC): {metadata.get('published_at_utc', '')}",
    ]
    if metadata.get("title"):
        sections.extend(["", "TITLE", metadata["title"]])
    if metadata.get("description"):
        sections.extend(["", "DESCRIPTION", metadata["description"]])
    if comments.get("comments"):
        rendered: list[str] = []
        for parent in comments["comments"]:
            rendered.append(
                f"[{parent.get('like_count', 0)} likes] {parent.get('author', '')}: "
                f"{parent.get('text', '')}"
            )
            for reply in parent.get("replies", []):
                rendered.append(
                    f"  reply [{reply.get('like_count', 0)} likes] {reply.get('author', '')}: "
                    f"{reply.get('text', '')}"
                )
        sections.extend(["", "TOP COMMENTS", "\n\n".join(rendered)])
    if transcript.get("status") == "complete" and transcript.get("text"):
        sections.extend(
            [
                "",
                f"TRANSCRIPT ({transcript.get('source', 'unknown')})",
                transcript["text"],
            ]
        )
    visual_text = (
        (video_dir / "ocr_text.txt").read_text(encoding="utf-8").strip()
        if (video_dir / "ocr_text.txt").exists()
        else ""
    )
    if visual_text:
        sections.extend(["", "ON-SCREEN TEXT", visual_text])
    (video_dir / "llm_input.txt").write_text(
        "\n".join(sections).rstrip() + "\n",
        encoding="utf-8",
    )
