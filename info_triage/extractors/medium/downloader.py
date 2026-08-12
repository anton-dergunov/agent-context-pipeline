"""Write Medium RSS article results as standalone local artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .direct import DirectClient, DirectError
from .feed import FeedError, MediumFeedClient
from .postprocess import html_to_markdown
from .urls import ArticleReference


@dataclass(frozen=True, slots=True)
class DownloadOptions:
    output_dir: Path


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def download_article(
    client: MediumFeedClient,
    reference: ArticleReference,
    options: DownloadOptions,
    *,
    direct_client: DirectClient | None = None,
) -> tuple[Path, bool]:
    """Retrieve one story directly, falling back to its rolling RSS feed."""
    article_dir = options.output_dir / reference.article_id
    article_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {
        "article_id": reference.article_id,
        "source_url": reference.source_url,
        "feed_url": reference.feed_url,
        "download": "started",
        "errors": [],
        "attempts": [],
    }
    _write_json(article_dir / "status.json", status)

    if direct_client is not None:
        try:
            article = direct_client.get_article(reference)
        except DirectError as exc:
            status["attempts"].append({"source": "direct", "result": exc.kind, "error": str(exc)})
        else:
            (article_dir / "article.md").write_text(article.markdown, encoding="utf-8")
            if article.html is not None:
                (article_dir / "response.html").write_text(article.html, encoding="utf-8")
            if article.raw_json is not None:
                _write_json(article_dir / "metadata_raw.json", article.raw_json)
            metadata = asdict(article)
            for key in ("markdown", "html", "raw_json"):
                metadata.pop(key)
            metadata.update(
                {
                    "schema_version": 1,
                    "requested_url": reference.source_url,
                    "feed_url": reference.feed_url,
                    "extraction_source": article.source,
                }
            )
            _write_json(article_dir / "metadata.json", metadata)
            status["attempts"].append({"source": article.source, "result": article.availability})
            status.update(
                {
                    "download": "complete" if article.availability == "full" else "preview",
                    "availability": article.availability,
                    "markdown_chars": len(article.markdown),
                    "markdown_words": len(article.markdown.split()),
                }
            )
            _write_json(article_dir / "status.json", status)
            return article_dir, True

    try:
        articles = client.get_articles(reference.feed_url)
    except FeedError as exc:
        status["attempts"].append({"source": "medium_rss", "result": exc.kind})
        status["download"] = exc.kind
        status["errors"].append({"stage": "feed", "error": str(exc)})
        _write_json(article_dir / "status.json", status)
        return article_dir, False

    article = next((item for item in articles if item.article_id == reference.article_id), None)
    if article is None:
        status["attempts"].append({"source": "medium_rss", "result": "not_found"})
        status.update(
            {
                "download": "not_found_in_feed",
                "feed_items_checked": len(articles),
                "note": "Medium RSS feeds are rolling and may no longer contain older stories.",
            }
        )
        _write_json(article_dir / "status.json", status)
        return article_dir, False

    try:
        markdown = html_to_markdown(article.html, source_url=article.url)
    except ValueError as exc:
        status["download"] = "failed"
        status["errors"].append({"stage": "postprocess", "error": str(exc)})
        _write_json(article_dir / "status.json", status)
        return article_dir, False

    (article_dir / "article.html").write_text(article.html, encoding="utf-8")
    (article_dir / "article.md").write_text(f"# {article.title}\n\n{markdown}", encoding="utf-8")
    metadata = asdict(article)
    metadata.pop("html")
    metadata.update(
        {
            "schema_version": 1,
            "requested_url": reference.source_url,
            "feed_url": reference.feed_url,
            "extraction_source": "medium_rss",
        }
    )
    _write_json(article_dir / "metadata.json", metadata)
    status.update(
        {
            "download": "complete" if article.availability == "full" else "preview",
            "availability": article.availability,
            "markdown_chars": len(markdown),
        }
    )
    status["attempts"].append({"source": "medium_rss", "result": article.availability})
    _write_json(article_dir / "status.json", status)
    return article_dir, True
