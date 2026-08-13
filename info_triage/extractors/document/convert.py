"""Local HTML and PDF to Markdown conversion."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from markitdown import MarkItDown
from pypdf import PdfReader
from trafilatura import extract
from trafilatura.settings import use_config

from .models import ExtractionError


def html_to_markdown(html: str, *, source_url: str | None = None) -> str:
    """Extract the main readable HTML content as Markdown."""
    config = use_config()
    config["DEFAULT"]["MIN_EXTRACTED_SIZE"] = "1"
    markdown = extract(
        html,
        url=source_url,
        output_format="markdown",
        include_comments=False,
        include_links=True,
        include_images=False,
        include_formatting=True,
        favor_recall=True,
        config=config,
    )
    if markdown is None or not markdown.strip():
        raise ExtractionError(
            "Trafilatura could not extract readable content", reason="html-no-extractable-text"
        )
    return markdown.strip() + "\n"


def pdf_to_markdown(body: bytes, *, max_pages: int = 500) -> tuple[str, int]:
    """Convert a text-bearing PDF locally without performing OCR."""
    try:
        reader = PdfReader(io.BytesIO(body), strict=False)
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception as exc:
                raise ExtractionError("PDF is encrypted", reason="pdf-encrypted") from exc
            if not unlocked:
                raise ExtractionError("PDF is encrypted", reason="pdf-encrypted")
        page_count = len(reader.pages)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"could not parse PDF: {exc}", reason="pdf-invalid") from exc
    if page_count > max_pages:
        raise ExtractionError(
            f"PDF has {page_count} pages; limit is {max_pages}", reason="pdf-page-limit"
        )

    try:
        with tempfile.TemporaryDirectory(prefix="info-triage-pdf-") as directory:
            path = Path(directory) / "source.pdf"
            path.write_bytes(body)
            result = MarkItDown(enable_plugins=False).convert(path)
            markdown = result.text_content.strip()
    except Exception as exc:
        raise ExtractionError(
            f"PDF conversion failed: {exc}", reason="pdf-conversion-failed"
        ) from exc
    if not markdown or len("".join(markdown.split())) < 20:
        raise ExtractionError(
            "PDF contains no useful embedded text; OCR is not enabled",
            reason="pdf-no-extractable-text",
        )
    return markdown + "\n", page_count
