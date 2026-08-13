"""Standalone provider-aware research extraction CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from .extractor import ResearchExtractor, ResearchOptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract a research paper and normalized metadata."
    )
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=Path, default=Path("url_output"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-html-bytes", type=int, default=10 * 1024 * 1024)
    parser.add_argument("--max-pdf-bytes", type=int, default=50 * 1024 * 1024)
    parser.add_argument("--max-pdf-pages", type=int, default=500)
    parser.add_argument("--no-keep-raw", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.retries < 0:
        parser.error("timeout must be positive and retries must be non-negative")
    if min(args.max_html_bytes, args.max_pdf_bytes, args.max_pdf_pages) < 1:
        parser.error("response and PDF limits must be positive")
    options = ResearchOptions(
        output_dir=args.output_dir,
        timeout=args.timeout,
        retries=args.retries,
        max_html_bytes=args.max_html_bytes,
        max_pdf_bytes=args.max_pdf_bytes,
        max_pdf_pages=args.max_pdf_pages,
        keep_raw=not args.no_keep_raw,
    )
    try:
        path, complete = ResearchExtractor(options).extract(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"{'saved' if complete else 'partial or failed'} -> {path}")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
