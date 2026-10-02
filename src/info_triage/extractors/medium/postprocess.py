"""Convert already-obtained article HTML to compact Markdown."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from info_triage.extractors.document.convert import html_to_markdown as extract_html


def clean_medium_html(html: str) -> str:
    """Remove Medium RSS tracking and syndication chrome from an HTML fragment."""
    soup = BeautifulSoup(html, "html.parser")
    for image in soup.select('img[src*="medium.com/_/stat"]'):
        image.decompose()
    for link_paragraph in soup.select("p.medium-feed-link"):
        link_paragraph.decompose()
    for paragraph in reversed(soup.find_all("p")):
        text = " ".join(paragraph.get_text(" ", strip=True).split())
        if "was originally published in" in text and text.endswith(
            "where people are continuing the conversation by highlighting and responding to this story."
        ):
            previous = paragraph.find_previous_sibling()
            paragraph.decompose()
            if previous is not None and previous.name == "hr":
                previous.decompose()
            break
    return str(soup)


def html_to_markdown(html: str, *, source_url: str | None = None) -> str:
    """Extract main content from supplied HTML and render it as Markdown."""
    cleaned = clean_medium_html(html)
    document = (
        cleaned
        if BeautifulSoup(cleaned, "html.parser").find("html")
        else f"<html><body><article>{cleaned}</article></body></html>"
    )
    try:
        return extract_html(document, source_url=source_url)
    except RuntimeError as exc:
        raise ValueError(
            "Trafilatura could not extract article content from the supplied HTML"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a locally supplied HTML document to Markdown with Trafilatura."
    )
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--source-url", help="original URL used only as extraction metadata")
    parser.add_argument("--output", type=Path, help="write Markdown here instead of stdout")
    args = parser.parse_args(argv)
    try:
        html = args.html_file.read_text(encoding="utf-8")
        markdown = html_to_markdown(html, source_url=args.source_url)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output is None:
        print(markdown, end="")
    else:
        if args.output.exists():
            print(f"error: output already exists: {args.output}", file=sys.stderr)
            return 1
        args.output.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
