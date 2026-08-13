"""Command-line interface for standalone YouTube extraction."""

from __future__ import annotations

import argparse
import gc
import os
import sys
from pathlib import Path

from info_triage.config import (
    ConfigError,
    YouTubeExtractorConfig,
    load_config,
)
from info_triage.extractors.instagram.ocr import OCREngine, make_engine
from info_triage.extractors.instagram.runtime import (
    apply_runtime_threads,
    platform_defaults,
    resolve_threads,
)
from info_triage.extractors.instagram.transcription import (
    BACKEND_CHOICES,
    MODEL_CHOICES,
    Transcriber,
    make_transcriber,
    resolve_backend_and_model,
    resolve_transcription_threads,
)

from .extractor import ExtractionOptions, YouTubeExtractor
from .runner import ManagedYtDlp, RunnerSettings
from .urls import load_inputs

ENGINE_CHOICES = ["best", "auto", "rapidocr", "surya", "vision", "tesseract"]
REC_SCRIPTS = {"latin", "cyrillic", "eslav", "ch", "en", "japan", "korean"}

DEFAULT_YOUTUBE_CONFIG = YouTubeExtractorConfig(
    max_attempts=3,
    retry_backoff_seconds=5,
    max_parent_comments=5,
    max_replies=10,
    max_replies_per_thread=2,
    update_channel="nightly",
    update_check_interval_hours=24,
    update_on_compatibility_error=True,
)


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _default_config_path() -> Path:
    return _path(os.environ.get("INFO_TRIAGE_CONFIG", "config.yaml"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youtube-extract",
        description="Extract YouTube metadata/captions and locally process Short media.",
    )
    parser.add_argument("urls", nargs="*", help="individual YouTube video or Short URLs")
    parser.add_argument("--input-file", type=_path, help="UTF-8 file containing one URL per line")
    parser.add_argument("--config", type=_path, default=_default_config_path())
    parser.add_argument("--output-dir", type=_path, default=_path("youtube_output"))
    parser.add_argument("--kind", choices=("auto", "short", "video"), default="auto")
    parser.add_argument("--max-comments", type=int, help="maximum top-level comments")
    parser.add_argument("--max-replies", type=int, help="maximum replies across selected comments")
    parser.add_argument(
        "--max-replies-per-thread", type=int, help="maximum replies retained under one comment"
    )
    parser.add_argument("--max-attempts", type=int, help="total yt-dlp command attempts")
    parser.add_argument("--retry-backoff-seconds", type=float)
    parser.add_argument("--update-channel", choices=("stable", "nightly", "master"))
    parser.add_argument("--update-check-interval-hours", type=float)
    parser.add_argument(
        "--update-on-compatibility-error",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--cookies-file", type=_path, help="Netscape/Mozilla cookie file")
    parser.add_argument(
        "--cookies-from-browser",
        help="yt-dlp browser cookie selector, for example safari or chrome:Profile 1",
    )
    parser.add_argument(
        "--extractor-args",
        action="append",
        default=[],
        help="advanced yt-dlp extractor argument; may be repeated",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="reuse Short media already present in the output directory",
    )
    parser.add_argument("--skip-comments", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--skip-transcript", action="store_true")

    parser.add_argument("--ocr-engine", choices=ENGINE_CHOICES, default="best")
    parser.add_argument("--video-ocr-engine", choices=ENGINE_CHOICES[1:])
    parser.add_argument("--model-cache-dir", type=_path, default=_path(".ocr_models"))
    parser.add_argument("--tesseract-languages")
    parser.add_argument("--rec-script", choices=["auto", *sorted(REC_SCRIPTS)], default="auto")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--video-mode", choices=("sample", "all"), default="sample")
    parser.add_argument("--video-sample-fps", type=float, default=3.0)
    parser.add_argument("--video-max-height", type=int, default=800)
    parser.add_argument("--ocr-batch-size", type=int, default=8)

    parser.add_argument("--transcription-backend", choices=BACKEND_CHOICES)
    parser.add_argument("--transcription-model", choices=MODEL_CHOICES)
    parser.add_argument("--transcription-language")
    parser.add_argument(
        "--transcription-model-cache-dir", type=_path, default=_path(".whisper_models")
    )
    parser.add_argument("--transcription-threads", type=int)
    parser.add_argument(
        "--transcription-vad",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    return parser


def _configuration(path: Path, parser: argparse.ArgumentParser):
    if not path.exists():
        if path == _default_config_path():
            return None
        parser.error(f"configuration file does not exist: {path}")
    try:
        return load_config(path)
    except ConfigError as error:
        parser.error(str(error))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _configuration(args.config, parser)
    youtube_config = config.youtube_extractor if config else DEFAULT_YOUTUBE_CONFIG
    data_dir = config.data_dir if config else _path("data")

    max_comments = (
        args.max_comments if args.max_comments is not None else youtube_config.max_parent_comments
    )
    max_replies = args.max_replies if args.max_replies is not None else youtube_config.max_replies
    max_replies_per_thread = (
        args.max_replies_per_thread
        if args.max_replies_per_thread is not None
        else youtube_config.max_replies_per_thread
    )
    max_attempts = (
        args.max_attempts if args.max_attempts is not None else youtube_config.max_attempts
    )
    retry_backoff = (
        args.retry_backoff_seconds
        if args.retry_backoff_seconds is not None
        else youtube_config.retry_backoff_seconds
    )
    update_channel = args.update_channel or youtube_config.update_channel
    update_interval = (
        args.update_check_interval_hours
        if args.update_check_interval_hours is not None
        else youtube_config.update_check_interval_hours
    )
    update_on_error = (
        args.update_on_compatibility_error
        if args.update_on_compatibility_error is not None
        else youtube_config.update_on_compatibility_error
    )

    if min(max_comments, max_replies, max_replies_per_thread) < 0:
        parser.error("comment and reply limits must be non-negative")
    if max_attempts < 1:
        parser.error("--max-attempts must be positive")
    if retry_backoff <= 0 or update_interval <= 0:
        parser.error("retry backoff and update interval must be positive")
    if args.video_sample_fps <= 0 or args.ocr_batch_size < 1:
        parser.error("video sample FPS and OCR batch size must be positive")
    if args.video_max_height < 0:
        parser.error("--video-max-height must be non-negative")
    if args.threads is not None and args.threads < 1:
        parser.error("--threads must be positive")
    if args.transcription_threads is not None and args.transcription_threads < 1:
        parser.error("--transcription-threads must be positive")

    try:
        references = load_inputs(args.urls, args.input_file)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    threads = resolve_threads(args.threads)
    transcription_threads = resolve_transcription_threads(args.transcription_threads)
    apply_runtime_threads(threads)
    scripts = None if args.rec_script == "auto" else (args.rec_script,)
    _, default_video_engine = platform_defaults()
    selected_video_engine = args.video_ocr_engine or (
        default_video_engine if args.ocr_engine == "best" else args.ocr_engine
    )

    def ocr_engine_factory() -> OCREngine:
        return make_engine(
            selected_video_engine,
            args.tesseract_languages,
            args.model_cache_dir,
            scripts=scripts,
            threads=threads,
        )

    try:
        transcription_backend, transcription_model = resolve_backend_and_model(
            args.transcription_backend, args.transcription_model
        )
    except ValueError as error:
        parser.error(str(error))

    def transcriber_factory() -> Transcriber:
        return make_transcriber(
            transcription_backend,
            transcription_model,
            args.transcription_model_cache_dir,
            transcription_threads,
        )

    runner = ManagedYtDlp(
        RunnerSettings(
            tool_dir=data_dir / "tools/yt-dlp",
            channel=update_channel,
            update_check_interval_hours=update_interval,
            update_on_compatibility_error=update_on_error,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff,
        )
    )
    options = ExtractionOptions(
        output_dir=args.output_dir,
        kind=args.kind,
        max_parent_comments=max_comments,
        max_replies=max_replies,
        max_replies_per_thread=max_replies_per_thread,
        skip_comments=args.skip_comments,
        skip_media_download=args.skip_download,
        skip_ocr=args.skip_ocr,
        skip_transcript=args.skip_transcript,
        video_mode=args.video_mode,
        video_sample_fps=args.video_sample_fps,
        video_max_height=args.video_max_height,
        ocr_batch_size=args.ocr_batch_size,
        transcription_language=args.transcription_language,
        transcription_vad=args.transcription_vad,
        cookies_file=args.cookies_file,
        cookies_from_browser=args.cookies_from_browser,
        extractor_args=tuple(args.extractor_args),
    )
    extractor = YouTubeExtractor(
        runner,
        options,
        ocr_engine_factory=None if args.skip_ocr else ocr_engine_factory,
        transcriber_factory=None if args.skip_transcript else transcriber_factory,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    try:
        for reference in references:
            print(f"Extracting {reference.video_id} …", flush=True)
            try:
                output = extractor.extract(reference)
                print(f"  {output}", flush=True)
            except Exception as error:
                failed = True
                print(
                    f"youtube-extract: {reference.video_id}: {type(error).__name__}: {error}",
                    file=sys.stderr,
                )
    finally:
        extractor.close()
        gc.collect()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
