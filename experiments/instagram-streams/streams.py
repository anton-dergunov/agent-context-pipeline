"""Where the words of a saved Instagram post are: caption, on-screen text, or speech.

Reads the Instagram extractions of a delivered inbox and prints aggregate counts only, so the
output can be committed while the posts themselves stay private. Run from the repository root:

    uv run python experiments/instagram-streams/streams.py ~/info-triage-inbox/info
"""

import json
import sys
from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}


def words(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").split()) if path.is_file() else 0


def main(inbox: Path) -> dict:
    posts = []
    for extraction in sorted(inbox.glob("*/extracted/*-instagram-*")):
        raw = extraction / "raw"
        media = list((raw / "media").glob("*")) if (raw / "media").is_dir() else []
        posts.append(
            {
                "caption": words(raw / "caption.txt"),
                "on_screen": words(raw / "ocr_text.txt"),
                "speech": words(raw / "transcript.txt"),
                "video": any(m.suffix.lower() in VIDEO_SUFFIXES for m in media),
                "images": sum(m.suffix.lower() not in VIDEO_SUFFIXES for m in media),
            }
        )

    def recovered(post: dict) -> int:
        return post["on_screen"] + post["speech"]

    with_media = [p for p in posts if p["video"] or p["images"]]
    return {
        "posts": len(posts),
        "posts_with_media_kept": len(with_media),
        "videos": sum(p["video"] for p in with_media),
        "image_posts": sum(not p["video"] for p in with_media),
        "carousels": sum(not p["video"] and p["images"] > 1 for p in with_media),
        "recovered_exceeds_caption": sum(recovered(p) > p["caption"] for p in posts),
        "recovered_at_least_3x_caption": sum(
            recovered(p) >= 3 * max(p["caption"], 1) for p in posts
        ),
        "caption_under_25_words": sum(p["caption"] < 25 for p in posts),
        "words": {
            "caption": sum(p["caption"] for p in posts),
            "on_screen": sum(p["on_screen"] for p in posts),
            "speech": sum(p["speech"] for p in posts),
        },
    }


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).expanduser()), indent=2))
