"""Generic linked HTML/PDF extraction and artifact generation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .client import DocumentClient
from .convert import html_to_markdown, pdf_to_markdown
from .io import artifact_staging, publish_directory, write_bytes, write_json, write_text
from .models import ExtractionError, FetchResult


@dataclass(frozen=True, slots=True)
class DocumentOptions:
    output_dir: Path = Path("url_output")
    timeout: float = 30.0
    retries: int = 2
    max_html_bytes: int = 10 * 1024 * 1024
    max_pdf_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 500
    keep_raw: bool = True


def normalized_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", query, ""))


def item_id(url: str) -> str:
    normalized = normalized_url(url)
    parsed = urlsplit(normalized)
    stem = Path(parsed.path.rstrip("/")).stem or "index"
    label = re.sub(r"[^a-z0-9]+", "-", f"{parsed.hostname}-{stem}".lower()).strip("-")
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:10]
    return f"{label[:70] or 'document'}-{digest}"


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"):
            return " ".join(str(tag["content"]).split())
    return None


def html_metadata(html: str, url: str) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    authors = [
        " ".join(str(tag.get("content")).split())
        for tag in soup.find_all("meta", attrs={"name": "citation_author"})
        if tag.get("content")
    ]
    return {
        "title": _meta(soup, "citation_title", "og:title", "twitter:title")
        or (" ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else None),
        "authors": authors or ([value] if (value := _meta(soup, "author")) else []),
        "description": _meta(soup, "description", "og:description"),
        "published_at": _meta(soup, "citation_publication_date", "article:published_time", "date"),
        "canonical_url": str(canonical.get("href")) if canonical and canonical.get("href") else url,
    }


class DocumentExtractor:
    def __init__(self, options: DocumentOptions, *, client: DocumentClient | None = None) -> None:
        self.options = options
        self.client = client or DocumentClient(
            timeout=options.timeout,
            retries=options.retries,
            max_html_bytes=options.max_html_bytes,
            max_pdf_bytes=options.max_pdf_bytes,
        )

    def extract(self, url: str, *, fetched: FetchResult | None = None) -> tuple[Path, bool]:
        result: FetchResult | None = fetched
        guessed_kind = "pdf" if urlsplit(url).path.lower().endswith(".pdf") else "html"
        directory = self.options.output_dir / guessed_kind / item_id(url)
        status: dict[str, object] = {
            "schema_version": 1,
            "requested_url": url,
            "status": "failed",
            "attempts": [],
        }
        try:
            result = result or self.client.fetch(url)
            directory = self.options.output_dir / result.kind / item_id(result.final_url)
            status["attempts"] = [{"method": result.method, "result": "fetched"}]
            metadata: dict[str, object] = {
                "schema_version": 1,
                "kind": result.kind,
                "requested_url": url,
                "final_url": result.final_url,
                "content_type": result.content_type,
                "content_bytes": len(result.body),
                "fetch_method": result.method,
            }
            if result.kind == "html":
                encoding = "utf-8"
                html = result.body.decode(encoding, errors="replace")
                markdown = html_to_markdown(html, source_url=result.final_url)
                metadata.update(html_metadata(html, result.final_url))
            elif result.kind == "pdf":
                markdown, pages = pdf_to_markdown(result.body, max_pages=self.options.max_pdf_pages)
                metadata["page_count"] = pages
                metadata["pdf_extraction_method"] = (
                    "pdfplumber-adaptive-spacing-with-pypdf-quality-fallback"
                )
            else:
                raise ExtractionError(
                    f"unsupported document kind: {result.kind}", reason="unsupported-content"
                )
            status.update(
                {
                    "status": "complete",
                    "final_url": result.final_url,
                    "kind": result.kind,
                    "markdown_chars": len(markdown),
                    "markdown_words": len(markdown.split()),
                }
            )
            with artifact_staging(directory) as staging:
                write_text(staging / "content.md", markdown)
                write_json(staging / "metadata.json", metadata)
                if self.options.keep_raw:
                    write_bytes(staging / f"source.{result.kind}", result.body)
                write_json(staging / "status.json", status)
                publish_directory(staging, directory)
            return directory, True
        except ExtractionError as exc:
            status.update({"reason": exc.reason, "error": str(exc), "failed_url": exc.url})
            with artifact_staging(directory) as staging:
                write_json(staging / "status.json", status)
                publish_directory(staging, directory)
            return directory, False
