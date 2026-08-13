from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from info_triage.extractors.document.convert import html_to_markdown, pdf_to_markdown
from info_triage.extractors.document.models import ExtractionError


def _blank_pdf(pages: int = 1) -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    writer.write(output)
    return output.getvalue()


def _encrypted_pdf() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    writer.write(output)
    return output.getvalue()


def test_generic_html_to_markdown_keeps_structure():
    markdown = html_to_markdown(
        "<html><body><article><h1>A paper</h1><p>Useful <b>content</b>.</p></article></body></html>",
        source_url="https://example.com/paper",
    )
    assert "# A paper" in markdown
    assert "**content**" in markdown


def test_pdf_to_markdown_enforces_page_limit():
    with pytest.raises(ExtractionError) as raised:
        pdf_to_markdown(_blank_pdf(2), max_pages=1)
    assert raised.value.reason == "pdf-page-limit"


def test_pdf_to_markdown_rejects_encrypted_pdf():
    with pytest.raises(ExtractionError) as raised:
        pdf_to_markdown(_encrypted_pdf())
    assert raised.value.reason == "pdf-encrypted"


def test_pdf_to_markdown_rejects_textless_pdf(monkeypatch):
    monkeypatch.setattr(
        "info_triage.extractors.document.convert.MarkItDown.convert",
        lambda *_args, **_kwargs: SimpleNamespace(text_content=""),
    )
    with pytest.raises(ExtractionError) as raised:
        pdf_to_markdown(_blank_pdf())
    assert raised.value.reason == "pdf-no-extractable-text"


def test_pdf_to_markdown_returns_text_and_count(monkeypatch):
    monkeypatch.setattr(
        "info_triage.extractors.document.convert.MarkItDown.convert",
        lambda *_args, **_kwargs: SimpleNamespace(
            text_content="# Paper\n\nEnough embedded text to be useful."
        ),
    )
    markdown, pages = pdf_to_markdown(_blank_pdf())
    assert markdown.startswith("# Paper")
    assert pages == 1
