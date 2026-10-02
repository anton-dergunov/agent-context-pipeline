"""Escaping helpers shared by everything that emits Markdown links."""

from __future__ import annotations


def escape_markdown_label(title: str) -> str:
    """Escape a link label so its text cannot reopen Markdown syntax."""
    escaped = title.replace("\\", "\\\\")
    for character in "[]*_`":
        escaped = escaped.replace(character, "\\" + character)
    return escaped


def escape_markdown_destination(url: str) -> str:
    """Escape a link destination so parentheses cannot end it early."""
    return url.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
