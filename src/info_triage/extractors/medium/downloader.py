"""Run ordered Medium extraction methods and write standalone artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from info_triage.extractors.artifacts import (
    CONTENT_NAME,
    METADATA_NAME,
    RAW_DIR,
    STATUS_NAME,
)

from .direct import DirectClient, DirectError
from .feed import FeedError, MediumFeedClient
from .postprocess import html_to_markdown
from .urls import ArticleReference


class ExtractionMethod(StrEnum):
    RSS = "rss"
    BROWSER = "browser"


DEFAULT_METHODS = (ExtractionMethod.RSS, ExtractionMethod.BROWSER)
# A member-wall preview is title plus a few opening paragraphs: enough to
# identify and route the article, not enough to judge it. It must say so.
PREVIEW_REASON = "medium-member-preview"
ARTIFACT_NAMES = (
    CONTENT_NAME,
    METADATA_NAME,
    f"{RAW_DIR}/article.html",
    f"{RAW_DIR}/metadata_raw.json",
    f"{RAW_DIR}/response.html",
)


@dataclass(frozen=True, slots=True)
class DownloadOptions:
    output_dir: Path
    methods: tuple[ExtractionMethod, ...] = DEFAULT_METHODS


def parse_methods(value: str) -> tuple[ExtractionMethod, ...]:
    """Parse an ordered comma-separated extraction method list."""
    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    if not names:
        raise ValueError("at least one Medium extraction method is required")
    if len(names) != len(set(names)):
        raise ValueError("Medium extraction methods must not be repeated")
    try:
        return tuple(ExtractionMethod(name) for name in names)
    except ValueError as exc:
        supported = ", ".join(method.value for method in ExtractionMethod)
        raise ValueError(f"unknown Medium extraction method; choose from: {supported}") from exc


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _clear_old_artifacts(article_dir: Path) -> None:
    for name in ARTIFACT_NAMES:
        (article_dir / name).unlink(missing_ok=True)


def _try_browser(
    direct_client: DirectClient | None,
    reference: ArticleReference,
    article_dir: Path,
    status: dict[str, Any],
) -> bool:
    if direct_client is None:
        status["attempts"].append(
            {"method": "browser", "result": "unavailable", "error": "client not configured"}
        )
        return False
    try:
        article = direct_client.get_article(reference)
    except DirectError as exc:
        status["attempts"].append({"method": "browser", "result": exc.kind, "error": str(exc)})
        status["errors"].append({"stage": "browser", "error": str(exc)})
        return False

    _write_text(article_dir / CONTENT_NAME, article.markdown)
    if article.html is not None:
        _write_text(article_dir / RAW_DIR / "response.html", article.html)
    if article.raw_json is not None:
        _write_json(article_dir / RAW_DIR / "metadata_raw.json", article.raw_json)
    metadata = asdict(article)
    for key in ("markdown", "html", "raw_json"):
        metadata.pop(key)
    metadata.update(
        {
            "schema_version": 1,
            "requested_url": reference.source_url,
            "feed_url": reference.feed_url,
            "extraction_method": "browser",
            "extraction_source": article.source,
        }
    )
    _write_json(article_dir / METADATA_NAME, metadata)
    status["attempts"].append(
        {"method": "browser", "source": article.source, "result": article.availability}
    )
    status.update(
        {
            "status": "complete" if article.availability == "full" else "partial",
            "reason": None if article.availability == "full" else PREVIEW_REASON,
            "availability": article.availability,
            "extraction_method": "browser",
            "markdown_chars": len(article.markdown),
            "markdown_words": len(article.markdown.split()),
        }
    )
    return True


def _try_rss(
    client: MediumFeedClient,
    reference: ArticleReference,
    article_dir: Path,
    status: dict[str, Any],
) -> bool:
    try:
        articles = client.get_articles(reference.feed_url)
    except FeedError as exc:
        status["attempts"].append({"method": "rss", "result": exc.kind, "error": str(exc)})
        status["errors"].append({"stage": "rss", "error": str(exc)})
        return False

    article = next((item for item in articles if item.article_id == reference.article_id), None)
    if article is None:
        status["attempts"].append(
            {
                "method": "rss",
                "result": "not_found",
                "feed_items_checked": len(articles),
            }
        )
        return False
    try:
        markdown = html_to_markdown(article.html, source_url=article.url)
    except ValueError as exc:
        status["attempts"].append({"method": "rss", "result": "failed", "error": str(exc)})
        status["errors"].append({"stage": "rss_postprocess", "error": str(exc)})
        return False

    rendered = f"# {article.title}\n\n{markdown}"
    _write_text(article_dir / RAW_DIR / "article.html", article.html)
    _write_text(article_dir / CONTENT_NAME, rendered)
    metadata = asdict(article)
    metadata.pop("html")
    metadata.update(
        {
            "schema_version": 1,
            "requested_url": reference.source_url,
            "feed_url": reference.feed_url,
            "extraction_method": "rss",
            "extraction_source": "medium_rss",
        }
    )
    _write_json(article_dir / METADATA_NAME, metadata)
    status["attempts"].append({"method": "rss", "result": article.availability})
    status.update(
        {
            "status": "complete" if article.availability == "full" else "partial",
            "reason": None if article.availability == "full" else PREVIEW_REASON,
            "availability": article.availability,
            "extraction_method": "rss",
            "markdown_chars": len(rendered),
            "markdown_words": len(rendered.split()),
        }
    )
    return True


def download_article(
    client: MediumFeedClient,
    reference: ArticleReference,
    options: DownloadOptions,
    *,
    direct_client: DirectClient | None = None,
) -> tuple[Path, bool]:
    """Try configured methods in order and stop after the first successful result."""
    article_dir = options.output_dir / reference.article_id
    article_dir.mkdir(parents=True, exist_ok=True)
    _clear_old_artifacts(article_dir)
    status: dict[str, Any] = {
        "article_id": reference.article_id,
        "source_url": reference.source_url,
        "feed_url": reference.feed_url,
        "configured_methods": [method.value for method in options.methods],
        "status": "started",
        "errors": [],
        "attempts": [],
    }
    _write_json(article_dir / STATUS_NAME, status)

    for method in options.methods:
        if method is ExtractionMethod.RSS:
            complete = _try_rss(client, reference, article_dir, status)
        else:
            complete = _try_browser(direct_client, reference, article_dir, status)
        if complete:
            _write_json(article_dir / STATUS_NAME, status)
            return article_dir, True

    # A refusal is a fact about the source that routing must weigh; a failure
    # only says this attempt did not work. Keep them apart.
    refused = any(
        "403" in str(item.get("error", "")) or "401" in str(item.get("error", ""))
        for item in status["attempts"]
    )
    status["status"] = "blocked" if refused else "failed"
    status["reason"] = "access-blocked" if refused else "article-unavailable"
    status["note"] = "No configured extraction method returned article content."
    _write_json(article_dir / STATUS_NAME, status)
    return article_dir, False
