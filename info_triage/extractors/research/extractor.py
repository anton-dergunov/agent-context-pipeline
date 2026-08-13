"""Provider-aware research paper extraction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from info_triage.extractors.document.client import DocumentClient
from info_triage.extractors.document.convert import html_to_markdown, pdf_to_markdown
from info_triage.extractors.document.io import (
    artifact_staging,
    publish_directory,
    write_bytes,
    write_json,
    write_text,
)
from info_triage.extractors.document.models import ExtractionError, FetchResult

from .providers import (
    BodyCandidate,
    ResearchReference,
    match_research_url,
    parse_html_metadata,
    parse_openreview_metadata,
)


@dataclass(frozen=True, slots=True)
class ResearchOptions:
    output_dir: Path = Path("url_output")
    timeout: float = 30.0
    retries: int = 2
    max_html_bytes: int = 10 * 1024 * 1024
    max_pdf_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 500
    keep_raw: bool = True


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")[:120] or "paper"


def paper_directory(root: Path, reference: ResearchReference) -> Path:
    return root / "research" / f"{reference.provider}-{_safe_id(reference.paper_id)}"


def _decode_html(result: FetchResult) -> str:
    return result.body.decode("utf-8", errors="replace")


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_render_value(item)}" for key, item in value.items() if item)
    return str(value)


def render_paper(metadata: dict[str, Any], body: str | None) -> str:
    """Render normalized paper metadata, abstract, and optional full body."""
    title = metadata.get("title") or f"Research paper {metadata['paper_id']}"
    sections = [f"# {title}"]
    labels = (
        ("Authors", "authors"),
        ("Provider", "provider"),
        ("Paper ID", "paper_id"),
        ("Canonical URL", "canonical_url"),
        ("Submitted", "submitted_at"),
        ("Published", "published_at"),
        ("Updated", "updated_at"),
        ("Subjects", "subjects"),
        ("Venue", "venue"),
        ("DOI", "doi"),
        ("Volume", "volume"),
        ("Pages", "pages"),
        ("Publisher", "publisher"),
        ("License", "license"),
    )
    lines = [
        f"- **{label}:** {_render_value(metadata[key])}"
        for label, key in labels
        if metadata.get(key)
    ]
    for key, value in (metadata.get("provider_metadata") or {}).items():
        if value:
            label = key.replace("_", " ").title()
            lines.append(f"- **{label}:** {_render_value(value)}")
    if lines:
        sections.append("\n".join(lines))
    if metadata.get("abstract"):
        sections.append(f"## Abstract\n\n{metadata['abstract']}")
    if body:
        sections.extend(["---", "## Full paper", body.strip()])
    return "\n\n".join(sections).rstrip() + "\n"


def _researchgate_pdf(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    selectors = (
        'meta[name="citation_pdf_url"]',
        'a[href*="fullTextFile"]',
        'a[href*="download"]',
        'a[href$=".pdf"]',
    )
    for selector in selectors:
        node = soup.select_one(selector)
        value = (
            node.get("content")
            if node and node.name == "meta"
            else node.get("href")
            if node
            else None
        )
        if value:
            return urljoin(page_url, str(value))
    return None


class ResearchExtractor:
    def __init__(self, options: ResearchOptions, *, client: DocumentClient | None = None) -> None:
        self.options = options
        self.client = client or DocumentClient(
            timeout=options.timeout,
            retries=options.retries,
            max_html_bytes=options.max_html_bytes,
            max_pdf_bytes=options.max_pdf_bytes,
        )

    def _metadata(
        self, reference: ResearchReference, directory: Path, status: dict[str, Any]
    ) -> tuple[dict[str, Any], FetchResult | None]:
        errors: list[dict[str, str]] = []
        for index, url in enumerate(reference.metadata_urls):
            try:
                result = self.client.fetch(url)
                if reference.provider == "openreview":
                    if result.kind != "json":
                        raise ExtractionError(
                            "OpenReview metadata endpoint did not return JSON",
                            reason="metadata-unavailable",
                        )
                    try:
                        payload = json.loads(result.body)
                        metadata = parse_openreview_metadata(reference, payload)
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise ExtractionError(str(exc), reason="metadata-unavailable") from exc
                    if self.options.keep_raw:
                        write_bytes(directory / "metadata-source.json", result.body)
                else:
                    if result.kind != "html":
                        raise ExtractionError(
                            "metadata page did not return HTML", reason="metadata-unavailable"
                        )
                    metadata = parse_html_metadata(
                        reference, _decode_html(result), result.final_url
                    )
                    if self.options.keep_raw:
                        write_bytes(directory / "metadata-source.html", result.body)
                if not metadata.get("title") and not metadata.get("abstract"):
                    raise ExtractionError(
                        "metadata page contained neither title nor abstract",
                        reason="metadata-unavailable",
                    )
                status["attempts"].append(
                    {"stage": "metadata", "url": url, "method": result.method, "result": "complete"}
                )
                return metadata, result
            except ExtractionError as exc:
                errors.append({"url": url, "reason": exc.reason, "error": str(exc)})
                status["attempts"].append(
                    {"stage": "metadata", "url": url, "result": "failed", "reason": exc.reason}
                )
        raise ExtractionError(
            "; ".join(item["error"] for item in errors) or "metadata unavailable",
            reason="metadata-unavailable",
        )

    def _body(
        self,
        reference: ResearchReference,
        metadata: dict[str, Any],
        metadata_result: FetchResult | None,
        directory: Path,
        status: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        candidates = list(reference.body_candidates)
        if pdf_url := metadata.get("pdf_url"):
            candidate = BodyCandidate("pdf", str(pdf_url))
            if candidate not in candidates:
                candidates.insert(0, candidate)
        if reference.provider == "researchgate" and metadata_result is not None:
            if pdf_url := _researchgate_pdf(
                _decode_html(metadata_result), metadata_result.final_url
            ):
                candidates.insert(0, BodyCandidate("pdf", pdf_url))
            candidates.append(BodyCandidate("html", metadata_result.final_url))

        errors: list[dict[str, str]] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            try:
                if metadata_result is not None and candidate.url == metadata_result.final_url:
                    result = metadata_result
                else:
                    result = self.client.fetch(candidate.url)
                if result.kind == "pdf":
                    markdown, pages = pdf_to_markdown(
                        result.body, max_pages=self.options.max_pdf_pages
                    )
                    details: dict[str, Any] = {"page_count": pages}
                    if self.options.keep_raw:
                        write_bytes(directory / "paper-source.pdf", result.body)
                elif result.kind == "html":
                    markdown = html_to_markdown(_decode_html(result), source_url=result.final_url)
                    details = {}
                    if self.options.keep_raw:
                        write_bytes(directory / "paper-source.html", result.body)
                else:
                    raise ExtractionError(
                        f"paper body returned {result.kind}", reason="unsupported-content"
                    )
                status["attempts"].append(
                    {
                        "stage": "body",
                        "url": candidate.url,
                        "method": result.method,
                        "format": result.kind,
                        "result": "complete",
                    }
                )
                return markdown, {
                    "body_url": result.final_url,
                    "body_format": result.kind,
                    "body_fetch_method": result.method,
                    **details,
                }
            except ExtractionError as exc:
                errors.append({"url": candidate.url, "reason": exc.reason, "error": str(exc)})
                status["attempts"].append(
                    {
                        "stage": "body",
                        "url": candidate.url,
                        "result": "failed",
                        "reason": exc.reason,
                    }
                )
        raise ExtractionError(
            "; ".join(item["error"] for item in errors) or "no paper body URL was available",
            reason="paper-body-unavailable",
        )

    def extract(self, value: str | ResearchReference) -> tuple[Path, bool]:
        reference = match_research_url(value) if isinstance(value, str) else value
        if reference is None:
            raise ValueError("not a supported research-paper URL")
        directory = paper_directory(self.options.output_dir, reference)
        status: dict[str, Any] = {
            "schema_version": 1,
            "status": "failed",
            "provider": reference.provider,
            "paper_id": reference.paper_id,
            "requested_url": reference.source_url,
            "attempts": [],
        }
        with artifact_staging(directory) as staging:
            try:
                metadata, metadata_result = self._metadata(reference, staging, status)
            except ExtractionError as exc:
                status.update({"reason": exc.reason, "error": str(exc)})
                write_json(staging / "status.json", status)
                publish_directory(staging, directory)
                return directory, False

            complete = True
            body: str | None = None
            try:
                body, details = self._body(reference, metadata, metadata_result, staging, status)
                metadata.update(details)
                status["status"] = "complete"
            except ExtractionError as exc:
                complete = False
                status.update({"status": "partial", "reason": exc.reason, "error": str(exc)})
            markdown = render_paper(metadata, body)
            write_text(staging / "paper.md", markdown)
            write_json(staging / "metadata.json", metadata)
            status.update(
                {"markdown_chars": len(markdown), "markdown_words": len(markdown.split())}
            )
            write_json(staging / "status.json", status)
            publish_directory(staging, directory)
            return directory, complete
