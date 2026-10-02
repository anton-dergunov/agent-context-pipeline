"""Run the extractors over an item's link table and cache what they retrieve.

Two things shape this module. First, extraction is the only expensive stage in
the pipeline, so its results are cached outside the item directory: an item is
re-materialized from `telegram.json` on every Telegram edit, and adding a note
to a message must not re-download a thirty-page PDF. Second, extraction never
blocks an item — every failure is recorded against its link and the item lands
with its capture text regardless.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import (
    InstagramExtractorConfig,
    MediumExtractorConfig,
    YouTubeExtractorConfig,
)
from .extractors.artifacts import (
    COMMENTS_NAME,
    CONTENT_NAME,
    METADATA_NAME,
    PAPER_PDF_NAME,
    RAW_DIR,
    STATUS_NAME,
    HarvestedLink,
)

EXTRACTED_DIR = "extracted"
CACHE_DIR = "extraction-cache"
CACHE_MANIFEST = "cache.json"
# Bumped when the extraction output shape changes, so stale entries are re-run
# rather than served in a layout the index no longer understands.
CACHE_VERSION = 1


@dataclass(frozen=True, slots=True)
class ExtractionSettings:
    """Everything the extractors need that is not the URL itself."""

    data_dir: Path
    keep_raw: bool = True
    youtube: YouTubeExtractorConfig | None = None
    instagram: InstagramExtractorConfig | None = None
    medium: MediumExtractorConfig | None = None
    ocr_model_cache_dir: Path = Path(".ocr_models")
    transcription_model_cache_dir: Path = Path(".whisper_models")


@dataclass
class ExtractionRecord:
    """One extraction, as the index needs to see it."""

    link_n: int
    handler: str
    identity: str
    directory: str
    status: str
    reason: str | None = None
    kind: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    published: str | None = None
    venue: str | None = None
    doi: str | None = None
    word_count: int = 0
    abstract: str | None = None
    # The opening prose of content.md, kept generously so the index can cut it
    # to whatever lead length is configured without re-reading the body.
    excerpt: str | None = None
    # content.md split into its `## ` sections, uncut. Short-form media carries
    # its payload in the recovered streams — on-screen text, spoken audio —
    # which a flat prefix of the file never reaches, so the index needs them
    # separable and labelled rather than run together.
    sections: list[tuple[str, str]] = field(default_factory=list)
    # Media whose content is on the screen and in the audio rather than in
    # prose: every Instagram post, and YouTube Shorts.
    short_form: bool = False
    # Path inside the extraction of a PDF kept for the reader, if one was saved.
    pdf: str | None = None
    via: str | None = None

    @property
    def retrieved(self) -> bool:
        return self.status in ("complete", "partial")

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_n": self.link_n,
            "handler": self.handler,
            "identity": self.identity,
            "directory": self.directory,
            "status": self.status,
            "reason": self.reason,
            "kind": self.kind,
            "title": self.title,
            "authors": self.authors,
            "published": self.published,
            "venue": self.venue,
            "doi": self.doi,
            "word_count": self.word_count,
            "via": self.via,
        }


class ExtractionError(RuntimeError):
    """An extractor could not be run at all, as opposed to failing to retrieve."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def cache_key(canonical_url: str) -> str:
    """Key extraction on the target, not on the item that happens to link to it."""
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    return digest[:16]


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return value


EXCERPT_WORDS = 400

_SECTION_HEADING = re.compile(r"^## +(.+?)\s*$")
#: A fact line under the title: "- Channel: …" or the papers' "- **Authors:** …".
_FACT_LINE = re.compile(r"^- \*{0,2}[^:*\n]{1,40}:")


def _word_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").split())
    except OSError:
        return 0


def _sections(path: Path) -> list[tuple[str, str]]:
    """Split a body into its ``## `` sections, past the title and fact list.

    Every extractor writes the same shape: an H1 title, a block of
    ``- Label: value`` facts the frontmatter already carries, then one ``## ``
    section per retrieved stream. Slicing on that structure is what keeps body
    bullets: the rule this replaces dropped every block beginning ``- `` and so
    deleted whole bulleted lists — on item 2026-08-14_150 the five nearby
    locations that were the point of the capture.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        heading = _SECTION_HEADING.match(line)
        if heading:
            sections.append((heading.group(1), []))
        else:
            sections[-1][1].append(line)
    # The preamble keeps only prose: the title and the facts are already indexed.
    preamble = [
        line for line in sections[0][1] if not line.startswith("# ") and not _FACT_LINE.match(line)
    ]
    sections[0] = ("", preamble)
    return [
        (heading, "\n".join(body).strip()) for heading, body in sections if "".join(body).strip()
    ]


def _excerpt(sections: Sequence[tuple[str, str]]) -> str | None:
    """Flatten the sections into opening prose, kept generously for the index."""
    paragraphs: list[str] = []
    used = 0
    for _, body in sections:
        for block in body.split("\n\n"):
            stripped = block.strip()
            if not stripped or stripped.startswith("#"):
                continue
            paragraphs.append(stripped)
            used += len(stripped.split())
            if used >= EXCERPT_WORDS:
                return "\n\n".join(paragraphs)
    return "\n\n".join(paragraphs) or None


def _author_name(value: Any) -> str:
    # LinkedIn and several HTML sources carry {"name": …, "url": …} rather than
    # a bare string; the URL is already the canonical link.
    if isinstance(value, dict):
        value = value.get("name")
    return str(value).strip() if isinstance(value, str) else ""


def _authors(value: Any) -> list[str]:
    if isinstance(value, list):
        names = [_author_name(item) for item in value]
    else:
        names = [_author_name(value)]
    return [name for name in names if name]


def _kind(handler: str, metadata: dict[str, Any], canonical_url: str) -> str:
    """Promote the item to what the top-priority extraction turned out to be."""
    from .links import is_repository_url

    if handler == "research":
        return "paper"
    if handler == "youtube":
        return "video"
    if handler in ("linkedin", "instagram"):
        return "post"
    if handler == "medium":
        return "article"
    if is_repository_url(canonical_url):
        return "repo"
    return "pdf" if metadata.get("kind") == "pdf" else "article"


def describe(
    directory: Path,
    *,
    link_n: int,
    handler: str,
    identity: str,
    relative_directory: str,
    canonical_url: str,
    via: str | None = None,
) -> ExtractionRecord:
    """Read one finished extraction directory into the record the index renders."""
    status = _read_json(directory / STATUS_NAME, {}) or {}
    metadata = _read_json(directory / METADATA_NAME, {}) or {}
    if not isinstance(status, dict):
        status = {}
    if not isinstance(metadata, dict):
        metadata = {}

    record = ExtractionRecord(
        link_n=link_n,
        handler=handler,
        identity=identity,
        directory=relative_directory,
        status=str(status.get("status") or "failed"),
        reason=status.get("reason") or None,
        via=via,
    )
    if not record.retrieved:
        return record

    record.kind = _kind(handler, metadata, canonical_url)
    # A LinkedIn post has no title of its own; its headline is what names it.
    record.title = metadata.get("title") or metadata.get("headline") or None
    record.authors = _authors(metadata.get("authors") or metadata.get("author"))
    record.published = _published(metadata)
    record.venue = metadata.get("venue") or metadata.get("channel") or None
    record.doi = metadata.get("doi") or None
    record.abstract = metadata.get("abstract") or None
    record.word_count = _word_count(directory / CONTENT_NAME)
    record.sections = _sections(directory / CONTENT_NAME)
    record.excerpt = _excerpt(record.sections)
    record.short_form = handler == "instagram" or (
        handler == "youtube" and str(metadata.get("kind") or "") == "short"
    )
    # The one file under raw/ the index points at: a person reading a paper
    # wants the typeset PDF, and otherwise would have to know it is there.
    pdf = Path(RAW_DIR) / PAPER_PDF_NAME
    record.pdf = str(pdf) if (directory / pdf).is_file() else None
    return record


def _published(metadata: dict[str, Any]) -> str | None:
    """Return the publication date, which is the currency signal `vet` needs.

    Providers disagree on the encoding as well as the separator: Medium reports
    epoch milliseconds, the rest report a string.
    """
    for key in ("published_at", "published_at_utc", "submitted_at", "created_at_utc"):
        value = metadata.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float) and value > 0:
            seconds = value / 1000 if value > 1e11 else float(value)
            try:
                return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")
            except (OSError, OverflowError, ValueError):
                continue
        if isinstance(value, str) and value.strip():
            # The time of day is noise; only the date informs a currency call.
            return value.strip()[:10].replace("/", "-")
    return None


class ContentExtractor:
    """Dispatch one canonical URL to its extractor, through the cache."""

    def __init__(self, settings: ExtractionSettings) -> None:
        self.settings = settings
        self._cache_root = settings.data_dir / CACHE_DIR

    def retrieve(self, canonical_url: str, handler: str, scratch: Path) -> Path:
        """Return a directory holding this URL's extraction, running it if needed.

        The returned path lives in the cache, so callers copy what they want out
        of it rather than mutating it.
        """
        entry = self._cache_root / cache_key(canonical_url)
        manifest = _read_json(entry / CACHE_MANIFEST)
        if isinstance(manifest, dict) and manifest.get("version") == CACHE_VERSION:
            output = entry / str(manifest.get("output", ""))
            if output.is_dir():
                return output

        if entry.exists():
            shutil.rmtree(entry)
        staging = scratch / cache_key(canonical_url)
        staging.mkdir(parents=True, exist_ok=True)
        output = self._run(canonical_url, handler, staging)

        entry.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(entry))
        # Written last: an entry without a manifest is a miss, so an interrupted
        # run is re-tried instead of served half-finished.
        (entry / CACHE_MANIFEST).write_text(
            json.dumps(
                {
                    "version": CACHE_VERSION,
                    "canonical_url": canonical_url,
                    "handler": handler,
                    "output": str(output.relative_to(staging)),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return entry / output.relative_to(staging)

    def _run(self, url: str, handler: str, root: Path) -> Path:
        if handler == "research":
            return self._research(url, root)
        if handler == "medium":
            return self._medium(url, root)
        if handler == "linkedin":
            return self._linkedin(url, root)
        if handler == "youtube":
            return self._youtube(url, root)
        if handler == "instagram":
            return self._instagram(url, root)
        return self._document(url, root)

    def _research(self, url: str, root: Path) -> Path:
        from .extractors.research.extractor import ResearchExtractor, ResearchOptions

        options = ResearchOptions(output_dir=root, keep_raw=self.settings.keep_raw)
        directory, _ = ResearchExtractor(options).extract(url)
        return directory

    def _document(self, url: str, root: Path) -> Path:
        from .extractors.document.extractor import DocumentExtractor, DocumentOptions

        options = DocumentOptions(output_dir=root, keep_raw=self.settings.keep_raw)
        directory, _ = DocumentExtractor(options).extract(url)
        return directory

    def _medium(self, url: str, root: Path) -> Path:
        from .extractors.medium.direct import DirectClient
        from .extractors.medium.downloader import DownloadOptions, download_article
        from .extractors.medium.feed import MediumFeedClient
        from .extractors.medium.urls import parse_article_url

        cookie_file = self.settings.medium.cookie_file if self.settings.medium else None
        directory, _ = download_article(
            MediumFeedClient(),
            parse_article_url(url),
            DownloadOptions(output_dir=root),
            direct_client=DirectClient(cookie_file=cookie_file),
        )
        return directory

    def _linkedin(self, url: str, root: Path) -> Path:
        from .extractors.linkedin.client import AnonymousClient
        from .extractors.linkedin.downloader import DownloadOptions, download_post
        from .extractors.linkedin.urls import parse_post_url

        directory, _ = download_post(
            AnonymousClient(),
            parse_post_url(url),
            DownloadOptions(output_dir=root),
        )
        return directory

    def _youtube(self, url: str, root: Path) -> Path:
        from .extractors.media.ocr import make_engine
        from .extractors.media.transcription import (
            make_transcriber,
            resolve_backend_and_model,
        )
        from .extractors.youtube.extractor import ExtractionOptions, YouTubeExtractor
        from .extractors.youtube.runner import ManagedYtDlp, RunnerSettings
        from .extractors.youtube.urls import parse_video_url

        config = self.settings.youtube
        if config is None:
            raise ExtractionError("YouTube extraction is not configured", reason="not-configured")
        runner = ManagedYtDlp(
            RunnerSettings(
                tool_dir=self.settings.data_dir / "tools/yt-dlp",
                channel=config.update_channel,
                update_check_interval_hours=config.update_check_interval_hours,
                update_on_compatibility_error=config.update_on_compatibility_error,
                max_attempts=config.max_attempts,
                retry_backoff_seconds=config.retry_backoff_seconds,
            )
        )
        backend, model = resolve_backend_and_model(None, None)
        extractor = YouTubeExtractor(
            runner,
            ExtractionOptions(
                output_dir=root,
                max_parent_comments=config.max_parent_comments,
                max_replies=config.max_replies,
                max_replies_per_thread=config.max_replies_per_thread,
            ),
            ocr_engine_factory=lambda: make_engine(
                "best", None, self.settings.ocr_model_cache_dir, media="video"
            ),
            transcriber_factory=lambda: make_transcriber(
                backend, model, self.settings.transcription_model_cache_dir, 1
            ),
        )
        try:
            return extractor.extract(parse_video_url(url))
        finally:
            extractor.close()

    def _instagram(self, url: str, root: Path) -> Path:
        from .extractors.instagram.analysis import (
            OCRSettings,
            TranscriptionSettings,
            ocr_post,
            transcribe_post,
        )
        from .extractors.instagram.downloader import DownloadOptions, download_post, make_loader
        from .extractors.instagram.urls import shortcode_from_url
        from .extractors.media.ocr import make_engine
        from .extractors.media.runtime import resolve_engine_name
        from .extractors.media.transcription import (
            make_transcriber,
            resolve_backend_and_model,
        )

        config = self.settings.instagram
        shortcode = shortcode_from_url(url)
        options = DownloadOptions(
            output_dir=root,
            session_file=config.session_file if config else None,
            instagram_user=config.instagram_user if config else None,
            cookies_file=config.cookies_file if config else None,
        )
        post_dir = download_post(make_loader(options), url, shortcode, options)

        # OCR and transcription enrich a post that has already been retrieved.
        # If an engine is unavailable the caption is still worth keeping, so
        # each pass degrades the status instead of discarding the extraction.
        def read_screen_text() -> None:
            # A carousel and a reel can want different engines on the same
            # machine, so resolve each and share one only when they agree.
            image_name = resolve_engine_name("best", "image")
            video_name = resolve_engine_name("best", "video")
            image_engine = make_engine(image_name, None, self.settings.ocr_model_cache_dir)
            video_engine = (
                image_engine
                if video_name == image_name
                else make_engine(video_name, None, self.settings.ocr_model_cache_dir)
            )
            ocr_post(
                post_dir,
                image_engine=image_engine,
                video_engine=video_engine,
                settings=OCRSettings(),
            )

        def read_speech() -> None:
            backend, model = resolve_backend_and_model(None, None)
            transcriber = make_transcriber(
                backend, model, self.settings.transcription_model_cache_dir, 1
            )
            try:
                transcribe_post(
                    post_dir,
                    transcriber=transcriber,
                    settings=TranscriptionSettings(backend=backend, model_name=model),
                )
            finally:
                if hasattr(transcriber, "close"):
                    transcriber.close()

        for stage, run in (("ocr", read_screen_text), ("transcript", read_speech)):
            try:
                run()
            except Exception as error:
                _degrade(post_dir, stage, error)
        return post_dir


def _degrade(directory: Path, stage: str, error: Exception) -> None:
    """Record an enrichment failure without discarding what was retrieved."""
    status = _read_json(directory / STATUS_NAME, {})
    if not isinstance(status, dict):
        status = {}
    status["status"] = "partial"
    status.setdefault("reason", f"{stage}-unavailable")
    status.setdefault("errors", []).append(
        {"stage": stage, "error": f"{type(error).__name__}: {error}"}
    )
    (directory / STATUS_NAME).write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# The one per-handler bound on how many links a finished extraction may offer.
# Description link piles are mostly sponsorship; the paper is near the top.
HARVEST_LIMIT = {"youtube": 3}


def harvest_links(handler: str, directory: Path) -> list[HarvestedLink]:
    """Return the links a finished extraction offers for one further round.

    Only wrappers harvest. A paper, a Medium article, an arbitrary web page and an
    Instagram post are terminal: their outbound links are a bibliography, a back
    catalogue, site navigation, or nothing at all — never what was saved.
    """
    if handler == "linkedin":
        from .extractors.linkedin.prepare import harvest_links as harvest

        return harvest(directory)
    if handler == "youtube":
        from .extractors.youtube.prepare import harvest_links as harvest

        return harvest(directory)
    return []


def committed_files(source: Path, relative_root: Path) -> list[tuple[Path, Path]]:
    """Pair every file in a finished extraction with its path inside the item."""
    return [
        (relative_root / path.relative_to(source), path)
        for path in sorted(source.rglob("*"))
        if path.is_file()
    ]


def extraction_directory_name(position: int, handler: str, identity: str) -> str:
    """Name an extraction so its order, handler and target read at a glance."""
    safe = "".join(
        character if character.isalnum() or character in "._-" else "-" for character in identity
    ).strip("-")[:80]
    return f"{position:02d}-{handler}-{safe}" if safe else f"{position:02d}-{handler}"


__all__ = [
    "COMMENTS_NAME",
    "CONTENT_NAME",
    "EXTRACTED_DIR",
    "HARVEST_LIMIT",
    "ContentExtractor",
    "ExtractionError",
    "ExtractionRecord",
    "ExtractionSettings",
    "HarvestedLink",
    "committed_files",
    "describe",
    "extraction_directory_name",
    "harvest_links",
]
