"""Local HTML and PDF to Markdown conversion."""

from __future__ import annotations

import io

import pdfplumber
from pypdf import PdfReader
from trafilatura import extract
from trafilatura.settings import use_config

from .models import ExtractionError

PDF_WORD_GAP_RATIO = 0.15
PDF_LINE_TOLERANCE = 3
PDF_DUPLICATE_GLYPH_RATIO = 0.20
PDF_DUPLICATE_GLYPH_MIN_LETTERS = 40


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


def _pypdf_page_text(reader: PdfReader, index: int) -> str:
    """Use pypdf's ordinary flow mode as a conservative per-page fallback."""
    try:
        return (reader.pages[index].extract_text() or "").strip()
    except Exception:
        return ""


def _has_duplicate_glyph_layer(text: str) -> bool:
    """Detect pages whose embedded text repeats nearly every alphabetic glyph."""
    letters = [character.lower() for character in text if character.isalpha()]
    if len(letters) < PDF_DUPLICATE_GLYPH_MIN_LETTERS:
        return False
    repeated_pairs = sum(left == right for left, right in zip(letters, letters[1:]))
    return repeated_pairs / len(letters) >= PDF_DUPLICATE_GLYPH_RATIO


def _pdfplumber_text(body: bytes, reader: PdfReader) -> str:
    """Extract text with a font-relative word-gap threshold.

    PDFMiner's fixed default gap is too large for many tightly kerned research
    papers and silently joins adjacent words. A ratio follows the current font
    size instead, while pypdf supplies a page fallback for unusual encodings.
    """
    pages: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            for index, page in enumerate(pdf.pages):
                try:
                    text = page.dedupe_chars().extract_text(
                        x_tolerance=None,
                        x_tolerance_ratio=PDF_WORD_GAP_RATIO,
                        y_tolerance=PDF_LINE_TOLERANCE,
                        layout=False,
                    )
                except Exception:
                    text = None
                if not text or _has_duplicate_glyph_layer(text):
                    text = _pypdf_page_text(reader, index) or text
                pages.append((text or "").strip())
                page.close()
    except Exception as exc:
        fallback = [_pypdf_page_text(reader, index) for index in range(len(reader.pages))]
        if not any(fallback):
            raise ExtractionError(
                f"PDF text extraction failed: {exc}", reason="pdf-conversion-failed"
            ) from exc
        pages = fallback
    return "\n\n".join(page for page in pages if page)


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

    markdown = _pdfplumber_text(body, reader).strip()
    if not markdown or len("".join(markdown.split())) < 20:
        raise ExtractionError(
            "PDF contains no useful embedded text; OCR is not enabled",
            reason="pdf-no-extractable-text",
        )
    return markdown + "\n", page_count
