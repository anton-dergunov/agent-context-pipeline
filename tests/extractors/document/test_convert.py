from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from info_triage.extractors.document.convert import (
    PDF_WORD_GAP_RATIO,
    _has_duplicate_glyph_layer,
    _pdfplumber_text,
    html_to_markdown,
    pdf_to_markdown,
)
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


def test_pdf_to_markdown_rejects_textless_pdf():
    with pytest.raises(ExtractionError) as raised:
        pdf_to_markdown(_blank_pdf())
    assert raised.value.reason == "pdf-no-extractable-text"


def test_pdf_to_markdown_returns_text_and_count(monkeypatch):
    monkeypatch.setattr(
        "info_triage.extractors.document.convert._pdfplumber_text",
        lambda *_args: "# Paper\n\nEnough embedded text to be useful.",
    )
    markdown, pages = pdf_to_markdown(_blank_pdf())
    assert markdown.startswith("# Paper")
    assert pages == 1


def test_pdfplumber_uses_font_relative_spacing(monkeypatch):
    captured = {}

    class Page:
        def dedupe_chars(self):
            return self

        def extract_text(self, **kwargs):
            captured.update(kwargs)
            return "Words retain their spaces in tightly kerned prose."

        def close(self):
            pass

    class PDF:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(
        "info_triage.extractors.document.convert.pdfplumber.open", lambda _body: PDF()
    )
    reader = type("Reader", (), {"pages": [object()]})()
    assert "retain their spaces" in _pdfplumber_text(b"pdf", reader)
    assert captured["x_tolerance"] is None
    assert captured["x_tolerance_ratio"] == PDF_WORD_GAP_RATIO


def test_pdfplumber_falls_back_when_glyph_layer_is_duplicated(monkeypatch):
    class Page:
        def dedupe_chars(self):
            return self

        def extract_text(self, **_kwargs):
            return "TThhiiss  ppaaggee  hhaass  aa  dduupplliiccaattee  ggllyypphh  llaayyeerr.."

        def close(self):
            pass

    class PDF:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(
        "info_triage.extractors.document.convert.pdfplumber.open", lambda _body: PDF()
    )
    monkeypatch.setattr(
        "info_triage.extractors.document.convert._pypdf_page_text",
        lambda *_args: "This page has a single readable glyph layer.",
    )
    reader = type("Reader", (), {"pages": [object()]})()
    assert _pdfplumber_text(b"pdf", reader) == "This page has a single readable glyph layer."


def test_duplicate_glyph_detection_ignores_normal_double_letters():
    assert not _has_duplicate_glyph_layer(
        "A successfully collected letter still contains ordinary double letters."
    )
