from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .client import AnonymousClient
from .downloader import DownloadOptions, download_post
from .urls import load_inputs


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-extract",
        description="Anonymously extract the public text, images, and selected comments from LinkedIn posts.",
    )
    parser.add_argument("urls", nargs="*", help="public LinkedIn post URLs")
    parser.add_argument("--input-file", type=_path, help="UTF-8 text file containing one URL per line")
    parser.add_argument("--output-dir", type=_path, default=_path("linkedin_output"))
    parser.add_argument("--max-comments", type=int, default=50, help="maximum public comments retained per post")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="seconds to wait between post-page requests (default: 1.0)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request read timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_comments < 0:
        parser.error("--max-comments must be non-negative")
    if args.request_delay < 0:
        parser.error("--request-delay must be non-negative")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        inputs = load_inputs(args.urls, args.input_file)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = AnonymousClient(timeout=args.timeout, retries=2)
    options = DownloadOptions(
        output_dir=args.output_dir,
        max_comments=args.max_comments,
        request_delay=args.request_delay,
    )
    all_complete = True
    for index, reference in enumerate(inputs):
        if index and args.request_delay:
            time.sleep(args.request_delay)
        print(f"Downloading public LinkedIn post {reference.post_id} …", flush=True)
        post_dir, complete = download_post(client, reference, options)
        status = json.loads((post_dir / "status.json").read_text(encoding="utf-8"))
        print(f"  {status['download']}: {post_dir}", flush=True)
        if not complete:
            all_complete = False
            for error in status.get("errors", []):
                print(f"  {error.get('stage')}: {error.get('error')}", file=sys.stderr)
    return 0 if all_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
