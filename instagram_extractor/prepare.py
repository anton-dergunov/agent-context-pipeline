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
    """Write compact structured and readable inputs without raw frame-level noise."""
    metadata = _read_json(post_dir / "metadata.json", {})
    comments = _read_json(post_dir / "comments.json", {"comments": [], "submitter_first_comment": None})
    ocr_items: list[dict[str, Any]] = []
    for path in sorted((post_dir / "ocr").glob("*.ocr.json")):
        result = _read_json(path, {})
        ocr_items.append(
            {
                "source_file": result.get("source_file"),
                "engine": result.get("engine"),
                "text_segments": result.get("llm_ready_segments", []),
            }
        )

    payload = {
        "schema_version": 1,
        "purpose": "Input prepared for a separate summarization/information-extraction model",
        "post": metadata,
        "submitter_first_comment": comments.get("submitter_first_comment"),
        "popular_comments": comments.get("comments", []),
        "comment_selection": {
            "selected_count": comments.get("selected_count", 0),
            "scanned_count": comments.get("scanned_count", 0),
            "scan_truncated": comments.get("scan_truncated"),
            "error": comments.get("error"),
        },
        "visual_text": ocr_items,
    }
    (post_dir / "llm_input.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    sections: list[str] = [
        "INSTAGRAM POST",
        f"URL: {metadata.get('source_url', '')}",
        f"Shortcode: {metadata.get('shortcode', post_dir.name)}",
        f"Owner: @{metadata.get('owner', {}).get('username', '')}",
        f"Created (UTC): {metadata.get('created_at_utc', '')}",
    ]
    if metadata.get("title"):
        sections.extend(["", "TITLE", metadata["title"]])
    if metadata.get("caption"):
        sections.extend(["", "CAPTION", metadata["caption"]])
    if metadata.get("accessibility_caption"):
        sections.extend(["", "ACCESSIBILITY DESCRIPTION", metadata["accessibility_caption"]])
    if metadata.get("location"):
        sections.extend(["", "LOCATION", json.dumps(metadata["location"], ensure_ascii=False)])
    if comments.get("submitter_first_comment"):
        sections.extend(["", "POST OWNER'S FIRST COMMENT", comments["submitter_first_comment"]["text"]])
    if comments.get("comments"):
        rendered = [
            f"[{item.get('likes_count', 0)} likes] @{item.get('owner_username', '')}: {item.get('text', '')}"
            for item in comments["comments"]
        ]
        sections.extend(["", "POPULAR COMMENTS", "\n\n".join(rendered)])
    visual_text = (post_dir / "ocr_text.txt").read_text(encoding="utf-8").strip() if (post_dir / "ocr_text.txt").exists() else ""
    if visual_text:
        sections.extend(["", "ON-SCREEN / IMAGE TEXT", visual_text])
    (post_dir / "llm_input.txt").write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")

