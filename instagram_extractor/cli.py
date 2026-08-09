from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .downloader import DownloadOptions, download_post, make_loader
from .ocr import OCREngine, make_engine, ocr_images, ocr_video, rededuplicate_ocr_result
from .prepare import prepare_llm_input
from .urls import load_inputs


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instagram-extract",
        description="Download Instagram posts and extract multilingual on-screen text locally.",
    )
    parser.add_argument("urls", nargs="*", help="Instagram post/reel URLs or bare shortcodes")
    parser.add_argument("--input-file", type=_path, help="UTF-8 text file containing one URL per line")
    parser.add_argument("--output-dir", type=_path, default=_path("instagram_output"))
    parser.add_argument("--max-comments", type=int, default=50, help="maximum ranked comments written per post")
    parser.add_argument(
        "--comment-scan-limit",
        type=int,
        default=0,
        help="comments/replies scanned before ranking; 0 scans all available comments",
    )
    parser.add_argument("--instagram-user", help="username associated with an Instaloader session")
    parser.add_argument("--session-file", type=_path, help="Instaloader session file")
    parser.add_argument("--cookies-file", type=_path, help="Netscape/Mozilla cookies.txt containing Instagram cookies")
    parser.add_argument("--cookies-from-browser", help="import the logged-in Instagram session from this browser")
    parser.add_argument("--skip-download", action="store_true", help="OCR media already present under the output directory")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--skip-image-ocr", action="store_true", help="leave existing still-image OCR untouched")
    parser.add_argument("--skip-video-ocr", action="store_true", help="leave existing video OCR untouched")
    parser.add_argument(
        "--rededuplicate-only",
        action="store_true",
        help="rebuild compact text from stored frame OCR without network access or model inference",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=["best", "auto", "surya", "vision", "tesseract"],
        default="best",
        help="best uses Surya for images and Apple Vision for video on macOS",
    )
    parser.add_argument("--model-cache-dir", type=_path, default=_path(".ocr_models"))
    parser.add_argument(
        "--image-ocr-engine",
        choices=["auto", "surya", "vision", "tesseract"],
        help="override the OCR engine for still images/carousel slides",
    )
    parser.add_argument(
        "--video-ocr-engine",
        choices=["auto", "surya", "vision", "tesseract"],
        help="override the OCR engine for video frames (surya gives broader language coverage but is slow)",
    )
    parser.add_argument("--tesseract-languages", help="Tesseract language/script list, joined with +")
    parser.add_argument(
        "--video-mode",
        choices=["all", "adaptive"],
        default="all",
        help="all OCRs every decoded frame; adaptive is a faster sampling tradeoff",
    )
    parser.add_argument("--video-sample-fps", type=float, default=2.0)
    parser.add_argument("--video-change-threshold", type=float, default=11.0)
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
    sources = sorted(path for path in media_dir.glob("*") if path.is_file() and not path.name.endswith(".part"))
    image_sources = [source for source in sources if source.suffix.lower() in IMAGE_SUFFIXES]
    if image_sources and image_engine is not None:
        try:
            outputs = [ocr_dir / f"{source.stem}.ocr.json" for source in image_sources]
            ocr_images(image_sources, outputs, image_engine, batch_size=args.ocr_batch_size)
            processed.extend({"file": source.name, "engine": image_engine.name} for source in image_sources)
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
                    change_threshold=args.video_change_threshold,
                    batch_size=args.ocr_batch_size,
                )
                processed.append({"file": source.name, "engine": video_engine.name})
        except Exception as exc:
            errors.append({"file": source.name, "error": f"{type(exc).__name__}: {exc}"})

    text_files = sorted(ocr_dir.glob("*.ocr.txt"))
    combined = "\n\n".join(path.read_text(encoding="utf-8").strip() for path in text_files if path.read_text(encoding="utf-8").strip())
    (post_dir / "ocr_text.txt").write_text(combined + ("\n" if combined else ""), encoding="utf-8")
    (ocr_dir / "status.json").write_text(
        json.dumps({"processed": processed, "errors": errors}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    prepare_llm_input(post_dir)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_comments < 0 or args.comment_scan_limit < 0:
        parser.error("--max-comments and --comment-scan-limit must be non-negative")
    if args.video_sample_fps <= 0:
        parser.error("--video-sample-fps must be positive")
    if args.ocr_batch_size < 1:
        parser.error("--ocr-batch-size must be positive")
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
            post_dir = download_post(loader, source_url, shortcode, options)
            status = json.loads((post_dir / "status.json").read_text(encoding="utf-8"))
            print(f"  {status['download']}: {post_dir}", flush=True)

    if not args.skip_ocr:
        media_files = [
            path
            for _, shortcode in inputs
            for path in (args.output_dir / shortcode / "media").glob("*")
            if path.is_file()
        ]
        needs_images = not args.skip_image_ocr and any(path.suffix.lower() in IMAGE_SUFFIXES for path in media_files)
        needs_videos = not args.skip_video_ocr and any(path.suffix.lower() in VIDEO_SUFFIXES for path in media_files)
        try:
            image_choice = args.image_ocr_engine or ("surya" if args.ocr_engine == "best" else args.ocr_engine)
            video_choice = args.video_ocr_engine or ("vision" if args.ocr_engine == "best" else args.ocr_engine)
            if needs_images and needs_videos and image_choice == video_choice:
                shared_engine = make_engine(image_choice, args.tesseract_languages, args.model_cache_dir)
                image_engine = shared_engine
                video_engine = shared_engine
            else:
                image_engine = (
                    make_engine(image_choice, args.tesseract_languages, args.model_cache_dir) if needs_images else None
                )
                video_engine = (
                    make_engine(video_choice, args.tesseract_languages, args.model_cache_dir) if needs_videos else None
                )
        except Exception as exc:
            parser.error(str(exc))
        engines = sorted({engine.name for engine in (image_engine, video_engine) if engine is not None})
        print(f"OCR engines: {', '.join(engines)}", flush=True)
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
