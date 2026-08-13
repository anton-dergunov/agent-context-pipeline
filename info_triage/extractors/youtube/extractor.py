"""Standalone YouTube extraction orchestration."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from info_triage.extractors.instagram.ocr import OCREngine, ocr_video
from info_triage.extractors.instagram.transcription import Transcriber

from .captions import CaptionResult, CaptionTrack, parse_json3, select_caption_track, unavailable
from .prepare import prepare_llm_input
from .runner import ManagedYtDlp, YtDlpError
from .urls import YouTubeReference, classify_video


@dataclass(slots=True)
class ExtractionOptions:
    output_dir: Path
    kind: str = "auto"
    max_parent_comments: int = 5
    max_replies: int = 10
    max_replies_per_thread: int = 2
    skip_comments: bool = False
    skip_media_download: bool = False
    skip_ocr: bool = False
    skip_transcript: bool = False
    video_mode: str = "sample"
    video_sample_fps: float = 3.0
    video_max_height: int = 800
    ocr_batch_size: int = 8
    transcription_language: str | None = None
    transcription_vad: bool = True
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None
    extractor_args: tuple[str, ...] = ()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _iso_timestamp(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


_SENSITIVE_RAW_KEYS = {
    "authorization",
    "cookie",
    "cookies",
    "fragment_base_url",
    "http_headers",
    "manifest_url",
    "url",
}


def sanitize_raw_metadata(value: Any, *, key: str | None = None) -> Any:
    """Retain yt-dlp's full response shape without persisting access-bearing values."""
    if key and key.casefold() in _SENSITIVE_RAW_KEYS:
        return "[redacted]"
    if isinstance(value, dict):
        return {name: sanitize_raw_metadata(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [sanitize_raw_metadata(item) for item in value]
    return value


def normalize_metadata(
    raw: dict[str, Any],
    reference: YouTubeReference,
    kind: str,
    kind_basis: str,
    yt_dlp_version: str,
    media: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep stable useful fields while retaining the complete sanitized payload separately."""
    return {
        "schema_version": 1,
        "source_url": reference.source_url,
        "canonical_url": reference.canonical_url,
        "video_id": reference.video_id,
        "kind": kind,
        "kind_basis": kind_basis,
        "yt_dlp_version": yt_dlp_version,
        "title": raw.get("title"),
        "description": raw.get("description") or "",
        "channel": raw.get("channel"),
        "channel_id": raw.get("channel_id"),
        "channel_url": raw.get("channel_url"),
        "channel_follower_count": raw.get("channel_follower_count"),
        "uploader": raw.get("uploader"),
        "uploader_id": raw.get("uploader_id"),
        "published_at_utc": _iso_timestamp(raw.get("timestamp")),
        "release_at_utc": _iso_timestamp(raw.get("release_timestamp")),
        "upload_date": raw.get("upload_date"),
        "duration_seconds": raw.get("duration"),
        "width": raw.get("width"),
        "height": raw.get("height"),
        "resolution": raw.get("resolution"),
        "aspect_ratio": raw.get("aspect_ratio"),
        "language": raw.get("language"),
        "original_language": raw.get("original_language"),
        "availability": raw.get("availability"),
        "license": raw.get("license"),
        "age_limit": raw.get("age_limit"),
        "live_status": raw.get("live_status"),
        "was_live": raw.get("was_live"),
        "view_count": raw.get("view_count"),
        "like_count": raw.get("like_count"),
        "comment_count": raw.get("comment_count"),
        "categories": raw.get("categories") or [],
        "tags": raw.get("tags") or [],
        "chapters": raw.get("chapters") or [],
        "heatmap": raw.get("heatmap") or [],
        "thumbnails": raw.get("thumbnails") or [],
        "available_subtitles": sorted((raw.get("subtitles") or {}).keys()),
        "available_automatic_captions": sorted((raw.get("automatic_captions") or {}).keys()),
        "track": raw.get("track"),
        "alt_title": raw.get("alt_title"),
        "creator": raw.get("creator"),
        "composers": raw.get("composers") or [],
        "artists": raw.get("artists") or [],
        "album": raw.get("album"),
        "genres": raw.get("genres") or [],
        "release_year": raw.get("release_year"),
        "series": raw.get("series"),
        "season": raw.get("season"),
        "episode": raw.get("episode"),
        "media": media,
    }


def _comment(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": value.get("id"),
        "author": value.get("author"),
        "author_id": value.get("author_id"),
        "author_url": value.get("author_url"),
        "author_is_uploader": bool(value.get("author_is_uploader")),
        "text": value.get("text") or "",
        "like_count": int(value.get("like_count") or 0),
        "timestamp": value.get("timestamp"),
        "created_at_utc": _iso_timestamp(value.get("timestamp")),
        "is_pinned": bool(value.get("is_pinned")),
        "is_favorited": bool(value.get("is_favorited")),
    }


def thread_comments(
    values: list[dict[str, Any]],
    max_parents: int,
    max_replies: int,
    max_replies_per_thread: int,
) -> dict[str, Any]:
    """Defensively enforce the same budgets requested from yt-dlp."""
    parents = [value for value in values if value.get("parent") in {None, "root"}][:max_parents]
    parent_ids = {str(value.get("id")) for value in parents}
    replies_by_parent: dict[str, list[dict[str, Any]]] = {key: [] for key in parent_ids}
    reply_count = 0
    for value in values:
        parent_id = str(value.get("parent"))
        if parent_id not in replies_by_parent or reply_count >= max_replies:
            continue
        replies = replies_by_parent[parent_id]
        if len(replies) >= max_replies_per_thread:
            continue
        replies.append(_comment(value))
        reply_count += 1

    threaded: list[dict[str, Any]] = []
    for value in parents:
        item = _comment(value)
        item["replies"] = replies_by_parent.get(str(value.get("id")), [])
        threaded.append(item)
    return {
        "selection_order": "YouTube top ranking",
        "max_parent_comments": max_parents,
        "max_replies": max_replies,
        "max_replies_per_thread": max_replies_per_thread,
        "parent_count": len(threaded),
        "reply_count": reply_count,
        "comments": threaded,
    }


class YouTubeExtractor:
    def __init__(
        self,
        runner: ManagedYtDlp,
        options: ExtractionOptions,
        *,
        ocr_engine_factory: Callable[[], OCREngine] | None = None,
        transcriber_factory: Callable[[], Transcriber] | None = None,
    ) -> None:
        self.runner = runner
        self.options = options
        self._ocr_engine_factory = ocr_engine_factory
        self._transcriber_factory = transcriber_factory
        self._ocr_engine: OCREngine | None = None
        self._transcriber: Transcriber | None = None

    def _common_arguments(self) -> list[str]:
        arguments = ["--ignore-config", "--no-playlist"]
        if self.options.cookies_file:
            arguments.extend(["--cookies", str(self.options.cookies_file)])
        if self.options.cookies_from_browser:
            arguments.extend(["--cookies-from-browser", self.options.cookies_from_browser])
        for value in self.options.extractor_args:
            arguments.extend(["--extractor-args", value])
        return arguments

    def _metadata(self, reference: YouTubeReference) -> dict[str, Any]:
        return self.runner.run_json(
            [
                *self._common_arguments(),
                "--skip-download",
                "--dump-single-json",
                reference.canonical_url,
            ]
        )

    def _comments(self, reference: YouTubeReference) -> dict[str, Any]:
        maximum = self.options.max_parent_comments + self.options.max_replies
        limits = (
            f"{maximum},{self.options.max_parent_comments},{self.options.max_replies},"
            f"{self.options.max_replies_per_thread},2"
        )
        raw = self.runner.run_json(
            [
                *self._common_arguments(),
                "--skip-download",
                "--write-comments",
                "--extractor-args",
                f"youtube:comment_sort=top;max_comments={limits}",
                "--dump-single-json",
                reference.canonical_url,
            ],
            check_updates=False,
        )
        comments = raw.get("comments")
        return thread_comments(
            comments if isinstance(comments, list) else [],
            self.options.max_parent_comments,
            self.options.max_replies,
            self.options.max_replies_per_thread,
        )

    def _download_caption(
        self,
        video_dir: Path,
        reference: YouTubeReference,
        track: CaptionTrack,
        duration: float | int | None,
    ) -> CaptionResult:
        with tempfile.TemporaryDirectory(prefix=".captions-", dir=video_dir) as temporary:
            output = str(Path(temporary) / "%(id)s.%(ext)s")
            mode = "--write-subs" if track.source == "youtube_manual" else "--write-auto-subs"
            self.runner.run(
                [
                    *self._common_arguments(),
                    "--skip-download",
                    mode,
                    "--sub-langs",
                    track.language,
                    "--sub-format",
                    "json3",
                    "--output",
                    output,
                    reference.canonical_url,
                ],
                check_updates=False,
            )
            files = sorted(Path(temporary).glob("*.json3"))
            if not files:
                return unavailable("caption_download_produced_no_json3")
            return parse_json3(files[0], track, duration)

    def _download_media(
        self,
        video_dir: Path,
        reference: YouTubeReference,
        *,
        needs_audio: bool,
    ) -> tuple[list[Path], list[dict[str, Any]], list[str]]:
        media_dir = video_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        if self.options.skip_media_download:
            sources = sorted(
                path
                for path in media_dir.iterdir()
                if path.is_file() and not path.name.endswith(".part")
            )
            return (
                sources,
                [{"file": str(path.relative_to(video_dir)), "role": path.stem} for path in sources],
                [],
            )

        combined_format = (
            f"b[height<={self.options.video_max_height}]"
            "[vcodec!=none][acodec!=none]/b[vcodec!=none][acodec!=none]"
        )
        try:
            self.runner.run(
                [
                    *self._common_arguments(),
                    "--format",
                    combined_format,
                    "--output",
                    str(media_dir / "video.%(ext)s"),
                    reference.canonical_url,
                ],
                check_updates=False,
            )
            sources = sorted(
                path
                for path in media_dir.glob("video.*")
                if path.is_file() and not path.name.endswith(".part")
            )
            return (
                sources,
                [
                    {"file": str(path.relative_to(video_dir)), "role": "combined_video"}
                    for path in sources
                ],
                [],
            )
        except YtDlpError:
            self.runner.run(
                [
                    *self._common_arguments(),
                    "--format",
                    f"bv[height<={self.options.video_max_height}]/bv",
                    "--output",
                    str(media_dir / "video.%(ext)s"),
                    reference.canonical_url,
                ],
                check_updates=False,
            )
            errors: list[str] = []
            if needs_audio:
                try:
                    self.runner.run(
                        [
                            *self._common_arguments(),
                            "--format",
                            "ba",
                            "--output",
                            str(media_dir / "audio.%(ext)s"),
                            reference.canonical_url,
                        ],
                        check_updates=False,
                    )
                except YtDlpError as error:
                    errors.append(f"audio_download_failed: {error}")
            sources = sorted(
                path
                for path in media_dir.iterdir()
                if path.is_file() and not path.name.endswith(".part")
            )
            manifest = [
                {
                    "file": str(path.relative_to(video_dir)),
                    "role": "audio" if path.stem == "audio" else "video_only",
                }
                for path in sources
            ]
            return sources, manifest, errors

    def _local_transcript(self, source: Path) -> dict[str, Any]:
        if self._transcriber is None:
            if self._transcriber_factory is None:
                return {
                    "status": "failed",
                    "source": "local_whisper",
                    "text": "",
                    "reason": "local_transcriber_unavailable",
                }
            self._transcriber = self._transcriber_factory()
        result = self._transcriber.transcribe(
            source,
            language=self.options.transcription_language,
            vad=self.options.transcription_vad,
            keep_segments=False,
        )
        return {
            "status": result.status,
            "source": "local_whisper",
            "backend": self._transcriber.backend,
            "model": self._transcriber.model_name,
            "language": result.language,
            "language_probability": result.language_probability,
            "text": result.text.strip(),
            "reason": result.error,
        }

    def _ocr(self, video_dir: Path, source: Path) -> dict[str, Any]:
        if self._ocr_engine is None:
            if self._ocr_engine_factory is None:
                raise RuntimeError("video OCR engine is unavailable")
            self._ocr_engine = self._ocr_engine_factory()
        output = video_dir / "ocr/video.ocr.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        ocr_video(
            source,
            output,
            self._ocr_engine,
            mode=self.options.video_mode,
            sample_fps=self.options.video_sample_fps,
            max_height=self.options.video_max_height,
            batch_size=self.options.ocr_batch_size,
        )
        text_path = output.with_suffix(".txt")
        text = text_path.read_text(encoding="utf-8").strip() if text_path.exists() else ""
        (video_dir / "ocr_text.txt").write_text(text + ("\n" if text else ""), encoding="utf-8")
        status = {
            "outcome": "complete",
            "processed": [{"file": source.name, "engine": self._ocr_engine.name}],
            "errors": [],
        }
        _write_json(video_dir / "ocr/status.json", status)
        return status

    def extract(self, reference: YouTubeReference) -> Path:
        event_start = len(self.runner.events)
        video_dir = self.options.output_dir / reference.video_id
        video_dir.mkdir(parents=True, exist_ok=True)
        status: dict[str, Any] = {
            "video_id": reference.video_id,
            "outcome": "started",
            "stages": {},
            "errors": [],
        }
        _write_json(video_dir / "status.json", status)

        try:
            raw = self._metadata(reference)
            status["stages"]["metadata"] = "complete"
        except Exception as error:
            status["outcome"] = "failed"
            status["stages"]["metadata"] = "failed"
            status["errors"].append(
                {"stage": "metadata", "error": f"{type(error).__name__}: {error}"}
            )
            status["yt_dlp_events"] = self.runner.events[event_start:]
            _write_json(video_dir / "status.json", status)
            raise

        kind, kind_basis = classify_video(reference, raw, self.options.kind)
        if raw.get("live_status") == "is_live":
            error = ValueError("live-in-progress YouTube streams are not supported")
            status["outcome"] = "failed"
            status["stages"]["validation"] = "failed"
            status["errors"].append({"stage": "validation", "error": f"ValueError: {error}"})
            status["yt_dlp_events"] = self.runner.events[event_start:]
            _write_json(video_dir / "status.json", status)
            raise error
        _write_json(video_dir / "metadata_raw.json", sanitize_raw_metadata(raw))
        description = raw.get("description") or ""
        (video_dir / "description.txt").write_text(
            description + ("\n" if description else ""), encoding="utf-8"
        )

        if self.options.skip_comments:
            comments = {"comments": [], "parent_count": 0, "reply_count": 0}
            status["stages"]["comments"] = "skipped"
        else:
            try:
                comments = self._comments(reference)
                status["stages"]["comments"] = "complete"
            except Exception as error:
                comments = {
                    "comments": [],
                    "parent_count": 0,
                    "reply_count": 0,
                    "error": str(error),
                }
                status["stages"]["comments"] = "failed"
                status["errors"].append(
                    {"stage": "comments", "error": f"{type(error).__name__}: {error}"}
                )
        _write_json(video_dir / "comments.json", comments)
        rendered_comments: list[str] = []
        for parent in comments.get("comments", []):
            rendered_comments.append(f"{parent.get('author', '')}: {parent.get('text', '')}")
            rendered_comments.extend(
                f"  {reply.get('author', '')}: {reply.get('text', '')}"
                for reply in parent.get("replies", [])
            )
        (video_dir / "comments.txt").write_text(
            "\n\n".join(rendered_comments) + ("\n" if rendered_comments else ""),
            encoding="utf-8",
        )

        caption: CaptionResult = unavailable("transcript_skipped")
        if not self.options.skip_transcript:
            track = select_caption_track(raw)
            if track:
                try:
                    caption = self._download_caption(
                        video_dir, reference, track, raw.get("duration")
                    )
                except Exception as error:
                    caption = unavailable(
                        f"caption_download_failed: {type(error).__name__}: {error}"
                    )
            else:
                caption = unavailable("no_original_language_captions")

        media_sources: list[Path] = []
        media_manifest: list[dict[str, Any]] = []
        if kind == "short":
            try:
                media_sources, media_manifest, media_errors = self._download_media(
                    video_dir,
                    reference,
                    needs_audio=caption.status != "complete" and not self.options.skip_transcript,
                )
                if not media_sources:
                    raise RuntimeError("Short media download produced no files")
                status["stages"]["media"] = "partial" if media_errors else "complete"
                status["errors"].extend(
                    {"stage": "media", "error": error} for error in media_errors
                )
            except Exception as error:
                status["stages"]["media"] = "failed"
                status["errors"].append(
                    {"stage": "media", "error": f"{type(error).__name__}: {error}"}
                )
        else:
            status["stages"]["media"] = "not_applicable"

        transcript: dict[str, Any] = caption.to_dict()
        if (
            kind == "short"
            and not self.options.skip_transcript
            and caption.status != "complete"
            and media_sources
        ):
            audio_source = next(
                (source for source in media_sources if source.stem == "audio"),
                next(
                    (source for source in media_sources if source.stem == "video"), media_sources[0]
                ),
            )
            try:
                transcript = self._local_transcript(audio_source)
            except Exception as error:
                transcript = {
                    "status": "failed",
                    "source": "local_whisper",
                    "text": "",
                    "reason": f"{type(error).__name__}: {error}",
                }
        _write_json(video_dir / "transcript.json", transcript)
        transcript_text = (
            transcript.get("text", "") if transcript.get("status") == "complete" else ""
        )
        (video_dir / "transcript.txt").write_text(
            transcript_text + ("\n" if transcript_text else ""), encoding="utf-8"
        )
        if self.options.skip_transcript:
            status["stages"]["transcript"] = "skipped"
        elif transcript.get("status") == "failed":
            status["stages"]["transcript"] = "failed"
            status["errors"].append(
                {"stage": "transcript", "error": transcript.get("reason") or "unknown failure"}
            )
        else:
            status["stages"]["transcript"] = transcript.get("status", "unavailable")

        video_source = next((source for source in media_sources if source.stem == "video"), None)
        if kind == "short" and not self.options.skip_ocr and video_source:
            try:
                self._ocr(video_dir, video_source)
                status["stages"]["ocr"] = "complete"
            except Exception as error:
                status["stages"]["ocr"] = "failed"
                status["errors"].append(
                    {"stage": "ocr", "error": f"{type(error).__name__}: {error}"}
                )
                _write_json(
                    video_dir / "ocr/status.json",
                    {"outcome": "failed", "processed": [], "errors": status["errors"][-1:]},
                )
        else:
            status["stages"]["ocr"] = "skipped" if kind == "short" else "not_applicable"

        metadata = normalize_metadata(
            raw,
            reference,
            kind,
            kind_basis,
            self.runner.current_version(),
            media_manifest,
        )
        _write_json(video_dir / "metadata.json", metadata)
        prepare_llm_input(video_dir)
        status["outcome"] = "partial" if status["errors"] else "complete"
        status["yt_dlp_version"] = self.runner.current_version()
        status["yt_dlp_events"] = self.runner.events[event_start:]
        _write_json(video_dir / "status.json", status)
        return video_dir

    def close(self) -> None:
        if self._transcriber is not None and hasattr(self._transcriber, "close"):
            self._transcriber.close()
