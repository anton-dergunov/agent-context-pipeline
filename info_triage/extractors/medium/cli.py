"""Command-line interface for layered Medium article retrieval."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .direct import DirectClient
from .downloader import DownloadOptions, download_article
from .feed import MediumFeedClient
from .urls import ArticleReference, parse_article_url, with_feed_url


def _input_urls(values: list[str], input_file: Path | None) -> list[str]:
    urls = list(values)
    if input_file is not None:
        for line in input_file.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                urls.append(value)
    return urls


def _references(values: list[str]) -> tuple[list[ArticleReference], list[str]]:
    result: list[ArticleReference] = []
    errors: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            reference = parse_article_url(value)
        except ValueError as exc:
            errors.append(f"{value}: {exc}")
            continue
        if reference.article_id not in seen:
            result.append(reference)
            seen.add(reference.article_id)
    return result, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract Medium articles directly, with RSS fallback and optional cookies."
    )
    parser.add_argument("urls", nargs="*", help="Medium article URLs")
    parser.add_argument("--input-file", type=Path, help="file containing one URL per line")
    parser.add_argument(
        "--feed-url",
        help="explicit documented author/publication feed to use for every supplied article",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("medium_output"))
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--cookie-file",
        type=Path,
        default=Path(os.environ["MEDIUM_COOKIE_FILE"])
        if os.environ.get("MEDIUM_COOKIE_FILE")
        else None,
        help="Netscape cookies.txt or browser JSON cookie export for member access",
    )
    parser.add_argument(
        "--cookies-from-browser",
        choices=("chrome", "chromium", "firefox", "edge", "brave", "opera", "vivaldi"),
        help="import a local browser's logged-in Medium session",
    )
    parser.add_argument(
        "--rss-only",
        action="store_true",
        help="skip direct page/structured retrieval and use documented RSS only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    values = _input_urls(args.urls, args.input_file)
    references, errors = _references(values)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if not references:
        print("error: no valid Medium article URLs supplied", file=sys.stderr)
        return 2
    if args.feed_url:
        try:
            references = [with_feed_url(reference, args.feed_url) for reference in references]
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.request_delay < 0 or args.timeout <= 0:
        print(
            "error: request delay must be non-negative and timeout must be positive",
            file=sys.stderr,
        )
        return 2

    client = MediumFeedClient(timeout=args.timeout)
    try:
        direct_client = (
            None
            if args.rss_only
            else DirectClient(
                timeout=args.timeout,
                cookie_file=args.cookie_file,
                cookies_from_browser=args.cookies_from_browser,
            )
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    options = DownloadOptions(output_dir=args.output_dir)
    successful = 0
    previous_feed: str | None = None
    for reference in references:
        if previous_feed is not None and reference.feed_url != previous_feed:
            time.sleep(args.request_delay)
        article_dir, ok = download_article(
            client,
            reference,
            options,
            direct_client=direct_client,
        )
        status = "saved" if ok else "unavailable"
        print(f"{reference.article_id}: {status} -> {article_dir}")
        successful += int(ok)
        previous_feed = reference.feed_url
    return 0 if successful == len(references) and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
