"""Ask each Instagram client for the same public posts, without an account.

    uv run python experiments/instagram-retrieval/probe.py SHORTCODE [SHORTCODE ...]

Each client runs out of process, the way it would be installed, so that a
client this project no longer depends on can still be measured. Results go to
`results.json` beside this script with the posts numbered, never named: which
posts someone saved is private even when the posts are public.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

INSTALOADER = """
import json, sys, instaloader
post = instaloader.Post.from_shortcode(instaloader.Instaloader(quiet=True).context, sys.argv[1])
print(json.dumps({"slides": post.mediacount, "caption_words": len((post.caption or "").split())}))
"""


def _instaloader(version: str) -> list[str]:
    return [
        "uvx",
        "--quiet",
        "--with",
        f"instaloader=={version}",
        "python",
        "-I",
        "-c",
        INSTALOADER,
    ]


def _clients(url: str, shortcode: str) -> dict[str, list[str]]:
    return {
        "instaloader 4.15.3": [*_instaloader("4.15.3"), shortcode],
        "instaloader 4.16": [*_instaloader("4.16"), shortcode],
        "gallery-dl": ["uvx", "--quiet", "gallery-dl", "-g", url],
        "yt-dlp": ["uv", "run", "yt-dlp", "-J", "--ignore-no-formats-error", url],
    }


def _summary(client: str, completed: subprocess.CompletedProcess[str]) -> dict:
    if completed.returncode != 0:
        lines = [line for line in completed.stderr.splitlines() if line.strip()]
        return {"ok": False, "error": (lines[-1] if lines else "no output")[:160]}
    if client == "gallery-dl":
        return {"ok": True, "slides": len(completed.stdout.split())}
    value = json.loads(completed.stdout.strip().splitlines()[-1])
    if client == "yt-dlp":
        entries = value.get("entries") or [value]
        return {
            "ok": True,
            "slides": len(entries),
            "caption_words": len((value.get("description") or "").split()),
            "comments": len(next((e.get("comments") or [] for e in entries if e), [])),
        }
    return {"ok": True, **value}


def main(shortcodes: list[str]) -> None:
    results = []
    for number, shortcode in enumerate(shortcodes, 1):
        url = f"https://www.instagram.com/p/{shortcode}/"
        for client, command in _clients(url, shortcode).items():
            completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
            # Shortcodes and URLs can appear in error text; keep neither.
            summary = _summary(client, completed)
            if "error" in summary:
                summary["error"] = summary["error"].replace(shortcode, "<post>")
            results.append({"post": number, "client": client, **summary})
            print(number, client, summary, flush=True)
    output = Path(__file__).with_name("results.json")
    output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
