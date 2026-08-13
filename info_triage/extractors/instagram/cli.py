"""Command-line interface for Instagram extraction."""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

from info_triage.config import ConfigError, load_config

from .downloader import DownloadOptions, download_post, make_loader
from .ocr import OCREngine, make_engine, ocr_images, ocr_video, rededuplicate_ocr_result
from .prepare import prepare_llm_input
from .runtime import apply_runtime_threads, platform_defaults, resolve_threads
from .transcription import (
    BACKEND_CHOICES,
    MODEL_CHOICES,
    Transcriber,
    TranscriptResult,
    has_audio_stream,
    make_transcriber,
    resolve_backend_and_model,
    resolve_transcription_threads,
    write_transcript_outputs,
)
from .urls import load_inputs

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}

ENGINE_CHOICES = ["best", "auto", "rapidocr", "surya", "vision", "tesseract"]
ENGINE_OVERRIDE_CHOICES = ["auto", "rapidocr", "surya", "vision", "tesseract"]
REC_SCRIPTS = {"latin", "cyrillic", "eslav", "ch", "en", "japan", "korean"}


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _env_path(name: str, default: str) -> Path:
    return _path(os.environ.get(name, default))


def _default_config_path() -> Path:
    return _path(os.environ.get("INFO_TRIAGE_CONFIG", "config.yaml"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instagram-extract",
        description="Download Instagram posts and extract multilingual visual and spoken text locally.",
    )
    parser.add_argument("urls", nargs="*", help="Instagram post/reel URLs or bare shortcodes")
    parser.add_argument(
        "--input-file", type=_path, help="UTF-8 text file containing one URL per line"
    )
    parser.add_argument("--config", type=_path, default=_default_config_path())
    parser.add_argument("--output-dir", type=_path, default=_path("instagram_output"))
    parser.add_argument("--max-attempts", type=int, help="total download attempts per post")
    parser.add_argument("--retry-backoff-seconds", type=float)
    parser.add_argument(
        "--max-comments", type=int, default=50, help="maximum ranked comments written per post"
    )
    parser.add_argument(
        "--comment-scan-limit",
        type=int,
        default=0,
        help="comments/replies scanned before ranking; 0 scans all available comments",
    )
    parser.add_argument("--instagram-user", help="username associated with an Instaloader session")
    parser.add_argument("--session-file", type=_path, help="Instaloader session file")
    parser.add_argument(
        "--cookies-file",
        type=_path,
        help="Netscape/Mozilla cookies.txt containing Instagram cookies",
    )
    parser.add_argument(
        "--cookies-from-browser", help="import the logged-in Instagram session from this browser"
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="process media already present under the output directory",
    )
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument(
        "--skip-transcription",
        action="store_true",
        help="leave existing speech transcripts untouched",
    )
    parser.add_argument(
        "--skip-image-ocr", action="store_true", help="leave existing still-image OCR untouched"
    )
    parser.add_argument(
        "--skip-video-ocr", action="store_true", help="leave existing video OCR untouched"
    )
    parser.add_argument(
        "--rededuplicate-only",
        action="store_true",
        help="rebuild compact text from stored frame OCR without network access or model inference",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=ENGINE_CHOICES,
        default="best",
        help="best picks per platform: Surya/Vision on macOS, RapidOCR elsewhere",
    )
    parser.add_argument(
        "--model-cache-dir",
        type=_path,
        default=_env_path("INSTAGRAM_OCR_MODEL_DIR", ".ocr_models"),
    )
    parser.add_argument(
        "--image-ocr-engine",
        choices=ENGINE_OVERRIDE_CHOICES,
        help="override the OCR engine for still images/carousel slides",
    )
    parser.add_argument(
        "--video-ocr-engine",
        choices=ENGINE_OVERRIDE_CHOICES,
        help="override the OCR engine for video frames (surya gives broader language coverage but is slow)",
    )
    parser.add_argument(
        "--tesseract-languages", help="Tesseract language/script list, joined with +"
    )
    parser.add_argument(
        "--rec-script",
        choices=["auto", *sorted(REC_SCRIPTS)],
        default="auto",
        help="RapidOCR recognition script; auto uses the configured default set",
    )
    parser.add_argument(
        "--threads",
        type=int,
        help="inference threads; defaults to INSTAGRAM_OCR_THREADS, the cgroup CPU limit, then all CPUs",
    )
    parser.add_argument(
        "--transcription-backend",
        choices=BACKEND_CHOICES,
        help="best uses MLX on Apple Silicon and faster-whisper CPU elsewhere",
    )
    parser.add_argument(
        "--transcription-model",
        choices=MODEL_CHOICES,
        help="default: medium on Apple Silicon Macs, small elsewhere",
    )
    parser.add_argument(
        "--transcription-language",
        help="spoken language code (for example en, es, ru, zh); omit for automatic detection",
    )
    parser.add_argument(
        "--transcription-model-cache-dir",
        type=_path,
        default=_env_path("INSTAGRAM_TRANSCRIPTION_MODEL_CACHE_DIR", ".whisper_models"),
    )
    parser.add_argument(
        "--transcription-threads",
        type=int,
        help="CPU helper threads; defaults to INSTAGRAM_TRANSCRIPTION_THREADS or 1",
    )
    parser.add_argument(
        "--transcription-vad",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="filter non-speech audio before transcription (enabled by default)",
    )
    parser.add_argument(
        "--video-mode",
        choices=["sample", "all"],
        default="sample",
        help="sample OCRs --video-sample-fps frames per second; all OCRs every decoded frame",
    )
    parser.add_argument(
        "--video-sample-fps",
        type=float,
        default=3.0,
        help="frames per second to OCR in sample mode (measured knee of the accuracy curve)",
    )
    parser.add_argument(
        "--video-max-height",
        type=int,
        default=800,
        help="downscale frames to at most this height before OCR (measured knee; 640 is faster, 0 disables)",
    )
    parser.add_argument("--ocr-batch-size", type=int, default=8)
    return parser


def _ocr_post(
    post_dir: Path,
    args: argparse.Namespace,
    image_engine: OCREngine | None,
    video_engine: OCREngine | None,
) -> None:
    media_dir = post_dir / "media"
    ocr_dir = post_dir / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, str]] = []
    processed: list[dict[str, str]] = []
    sources = sorted(
        path for path in media_dir.glob("*") if path.is_file() and not path.name.endswith(".part")
    )
    image_sources = [source for source in sources if source.suffix.lower() in IMAGE_SUFFIXES]
    if image_sources and image_engine is not None:
        try:
            outputs = [ocr_dir / f"{source.stem}.ocr.json" for source in image_sources]
            ocr_images(image_sources, outputs, image_engine, batch_size=args.ocr_batch_size)
            processed.extend(
                {"file": source.name, "engine": image_engine.name} for source in image_sources
            )
        except Exception as exc:
            for source in image_sources:
                errors.append({"file": source.name, "error": f"{type(exc).__name__}: {exc}"})

    for source in sources:
        output = ocr_dir / f"{source.stem}.ocr.json"
        try:
            if source.suffix.lower() in VIDEO_SUFFIXES:
                if video_engine is None:
                    continue
                ocr_video(
                    source,
                    output,
                    video_engine,
                    mode=args.video_mode,
                    sample_fps=args.video_sample_fps,
                    max_height=args.video_max_height,
                    batch_size=args.ocr_batch_size,
                )
                processed.append({"file": source.name, "engine": video_engine.name})
        except Exception as exc:
            errors.append({"file": source.name, "error": f"{type(exc).__name__}: {exc}"})

    text_files = sorted(ocr_dir.glob("*.ocr.txt"))
    combined = "\n\n".join(
        path.read_text(encoding="utf-8").strip()
        for path in text_files
        if path.read_text(encoding="utf-8").strip()
    )
    (post_dir / "ocr_text.txt").write_text(combined + ("\n" if combined else ""), encoding="utf-8")
    (ocr_dir / "status.json").write_text(
        json.dumps({"processed": processed, "errors": errors}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prepare_llm_input(post_dir)


def _transcribe_post(
    post_dir: Path,
    args: argparse.Namespace,
    transcriber: Transcriber | None,
    *,
    backend: str,
    model_name: str,
) -> None:
    media_dir = post_dir / "media"
    sources = sorted(
        path
        for path in media_dir.glob("*")
        if path.is_file()
        and path.suffix.lower() in VIDEO_SUFFIXES
        and not path.name.endswith(".part")
    )
    results: list[TranscriptResult] = []
    for source in sources:
        if transcriber is None:
            results.append(TranscriptResult(source_file=str(source), status="no_audio"))
        else:
            results.append(
                transcriber.transcribe(
                    source,
                    language=args.transcription_language,
                    vad=args.transcription_vad,
                    keep_segments=False,
                )
            )
    write_transcript_outputs(post_dir, results, backend=backend, model_name=model_name)
    prepare_llm_input(post_dir)


def _retryable_download_status(status: dict) -> bool:
    if status.get("download") != "failed" and status.get("comments") != "failed":
        return False
    message = " ".join(str(item.get("error", "")) for item in status.get("errors", [])).casefold()
    non_retryable = (
        "login required",
        "not found",
        "private",
        "does not exist",
        "invalid",
        "cookies belong to",
    )
    return not any(marker in message for marker in non_retryable)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    extractor_config = None
    if args.config.exists():
        try:
            extractor_config = load_config(args.config).instagram_extractor
        except ConfigError as exc:
            parser.error(str(exc))
    max_attempts = (
        args.max_attempts
        if args.max_attempts is not None
        else extractor_config.max_attempts
        if extractor_config
        else 3
    )
    retry_backoff_seconds = (
        args.retry_backoff_seconds
        if args.retry_backoff_seconds is not None
        else extractor_config.retry_backoff_seconds
        if extractor_config
        else 5.0
    )
    if args.max_comments < 0 or args.comment_scan_limit < 0:
        parser.error("--max-comments and --comment-scan-limit must be non-negative")
    if max_attempts < 1 or retry_backoff_seconds <= 0:
        parser.error("retry attempts and backoff must be positive")
    if args.video_sample_fps <= 0:
        parser.error("--video-sample-fps must be positive")
    if args.ocr_batch_size < 1:
        parser.error("--ocr-batch-size must be positive")
    if args.video_max_height < 0:
        parser.error("--video-max-height must be non-negative")
    if args.threads is not None and args.threads < 1:
        parser.error("--threads must be positive")
    if args.transcription_threads is not None and args.transcription_threads < 1:
        parser.error("--transcription-threads must be positive")

    threads = resolve_threads(args.threads)
    transcription_threads = resolve_transcription_threads(args.transcription_threads)
    apply_runtime_threads(threads)
    try:
        inputs = load_inputs(args.urls, args.input_file)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.rededuplicate_only:
        for _, shortcode in inputs:
            post_dir = args.output_dir / shortcode
            for path in sorted((post_dir / "ocr").glob("*.ocr.json")):
                rededuplicate_ocr_result(path)
            text_files = sorted((post_dir / "ocr").glob("*.ocr.txt"))
            combined = "\n\n".join(
                path.read_text(encoding="utf-8").strip()
                for path in text_files
                if path.read_text(encoding="utf-8").strip()
            )
            (post_dir / "ocr_text.txt").write_text(
                combined + ("\n" if combined else ""), encoding="utf-8"
            )
            prepare_llm_input(post_dir)
        return 0

    options = DownloadOptions(
        output_dir=args.output_dir,
        max_comments=args.max_comments,
        comment_scan_limit=args.comment_scan_limit,
        session_file=args.session_file,
        instagram_user=args.instagram_user,
        cookies_file=args.cookies_file,
        cookies_from_browser=args.cookies_from_browser,
    )
    if not args.skip_download:
        try:
            loader = make_loader(options)
        except Exception as exc:
            parser.error(f"could not initialize Instagram session: {exc}")
        for source_url, shortcode in inputs:
            print(f"Downloading {shortcode} …", flush=True)
            for attempt in range(max_attempts):
                post_dir = download_post(loader, source_url, shortcode, options)
                status = json.loads((post_dir / "status.json").read_text(encoding="utf-8"))
                if not _retryable_download_status(status) or attempt + 1 >= max_attempts:
                    break
                time.sleep(retry_backoff_seconds * (2**attempt))
            status["attempts"] = attempt + 1
            (post_dir / "status.json").write_text(
                json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"  {status['download']}: {post_dir}", flush=True)

    if not args.skip_transcription:
        try:
            transcription_backend, transcription_model = resolve_backend_and_model(
                args.transcription_backend,
                args.transcription_model,
            )
        except ValueError as exc:
            parser.error(str(exc))
        media_files = [
            path
            for _, shortcode in inputs
            for path in (args.output_dir / shortcode / "media").glob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
        ]
        needs_model = False
        for path in media_files:
            try:
                needs_model = needs_model or has_audio_stream(path)
            except Exception:
                # Let the normal per-file result record a useful decode error.
                needs_model = True
        transcriber: Transcriber | None = None
        if needs_model:
            try:
                transcriber = make_transcriber(
                    transcription_backend,
                    transcription_model,
                    args.transcription_model_cache_dir,
                    transcription_threads,
                )
            except Exception as exc:
                parser.error(str(exc))
        print(
            f"Transcription: {transcription_backend}/{transcription_model} "
            f"({transcription_threads} CPU thread(s), VAD {'on' if args.transcription_vad else 'off'})",
            flush=True,
        )
        for _, shortcode in inputs:
            post_dir = args.output_dir / shortcode
            if not (post_dir / "media").exists():
                print(
                    f"Skipping transcription for {shortcode}: no media directory", file=sys.stderr
                )
                continue
            print(f"Transcribing {shortcode} …", flush=True)
            _transcribe_post(
                post_dir,
                args,
                transcriber,
                backend=transcription_backend,
                model_name=transcription_model,
            )
        if transcriber is not None and hasattr(transcriber, "close"):
            transcriber.close()
        del transcriber
        gc.collect()

    if not args.skip_ocr:
        media_files = [
            path
            for _, shortcode in inputs
            for path in (args.output_dir / shortcode / "media").glob("*")
            if path.is_file()
        ]
        needs_images = not args.skip_image_ocr and any(
            path.suffix.lower() in IMAGE_SUFFIXES for path in media_files
        )
        needs_videos = not args.skip_video_ocr and any(
            path.suffix.lower() in VIDEO_SUFFIXES for path in media_files
        )
        default_image, default_video = platform_defaults()
        scripts = None if args.rec_script == "auto" else (args.rec_script,)

        def build(choice: str) -> OCREngine:
            return make_engine(
                choice,
                args.tesseract_languages,
                args.model_cache_dir,
                scripts=scripts,
                threads=threads,
            )

        try:
            image_choice = args.image_ocr_engine or (
                default_image if args.ocr_engine == "best" else args.ocr_engine
            )
            video_choice = args.video_ocr_engine or (
                default_video if args.ocr_engine == "best" else args.ocr_engine
            )
            if needs_images and needs_videos and image_choice == video_choice:
                shared_engine = build(image_choice)
                image_engine = shared_engine
                video_engine = shared_engine
            else:
                image_engine = build(image_choice) if needs_images else None
                video_engine = build(video_choice) if needs_videos else None
        except Exception as exc:
            parser.error(str(exc))
        engines = sorted(
            {engine.name for engine in (image_engine, video_engine) if engine is not None}
        )
        print(f"OCR engines: {', '.join(engines)} ({threads} thread(s))", flush=True)
        for _, shortcode in inputs:
            post_dir = args.output_dir / shortcode
            if not (post_dir / "media").exists():
                print(f"Skipping OCR for {shortcode}: no media directory", file=sys.stderr)
                continue
            print(f"OCR {shortcode} …", flush=True)
            _ocr_post(post_dir, args, image_engine, video_engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
