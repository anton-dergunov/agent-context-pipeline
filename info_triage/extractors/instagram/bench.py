"""Benchmark the Instagram video OCR pipeline and report cost per minute.

Run the same command on every target machine (Mac, Synology NAS, Raspberry Pi)
and compare ``seconds_per_video_minute``. Results are appended to a JSON file so
runs from different machines can sit side by side.
"""

from __future__ import annotations

import argparse
import glob
import json
import platform
import resource
import sys
import tempfile
import time
from pathlib import Path

from . import runtime


def peak_rss_mb() -> float:
    """Peak resident set size. ru_maxrss is bytes on macOS, kibibytes on Linux."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _video_duration(path: Path) -> float:
    import cv2

    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        return float(frames) / fps if fps else 0.0
    finally:
        capture.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instagram-ocr-bench",
        description="Measure OCR throughput per minute of video on this machine.",
    )
    parser.add_argument(
        "videos", nargs="*", help="video files; defaults to instagram_output/*/media/*.mp4"
    )
    parser.add_argument("--engine", default="best", help="best, rapidocr, vision, surya, tesseract")
    parser.add_argument("--rec-script", default="auto")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--video-mode", choices=["sample", "all"], default="sample")
    parser.add_argument("--video-sample-fps", type=float, default=3.0)
    parser.add_argument("--video-max-height", type=int, default=800)
    parser.add_argument("--ocr-batch-size", type=int, default=8)
    parser.add_argument(
        "--limit-seconds", type=float, default=0.0, help="stop after this much video (0 = all)"
    )
    parser.add_argument("--output", type=Path, default=Path("bench_results.json"))
    parser.add_argument("--label", default="", help="free-form note stored with the result")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    threads = runtime.resolve_threads(args.threads)
    runtime.apply_runtime_threads(threads)

    from .engines import make_engine
    from .ocr import ocr_video

    videos = [Path(v) for v in (args.videos or sorted(glob.glob("instagram_output/*/media/*.mp4")))]
    videos = [v for v in videos if v.exists()]
    if not videos:
        print("no videos found; pass paths explicitly", file=sys.stderr)
        return 2

    engine_name = args.engine
    if engine_name == "best":
        engine_name = runtime.platform_defaults()[1]
    scripts = None if args.rec_script == "auto" else (args.rec_script,)

    load_start = time.time()
    engine = make_engine(engine_name, None, Path(".ocr_models"), scripts=scripts, threads=threads)
    load_seconds = time.time() - load_start

    per_video = []
    total_video_seconds = 0.0
    total_wall = 0.0
    total_frames = 0
    # Benchmark output is throwaway; do not litter the working directory or
    # require it to be writable.
    scratch_dir = tempfile.TemporaryDirectory(prefix="instagram-ocr-bench-")
    scratch = Path(scratch_dir.name)

    for video in videos:
        duration = _video_duration(video)
        if args.limit_seconds and total_video_seconds >= args.limit_seconds:
            break
        started = time.time()
        output = scratch / (video.stem + ".ocr.json")
        ocr_video(
            video,
            output,
            engine,
            mode=args.video_mode,
            sample_fps=args.video_sample_fps,
            max_height=args.video_max_height,
            batch_size=args.ocr_batch_size,
        )
        wall = time.time() - started
        payload = json.loads(output.read_text(encoding="utf-8"))
        frames_ocrd = int(payload.get("frames_ocrd") or 0)
        total_video_seconds += duration
        total_wall += wall
        total_frames += frames_ocrd
        per_video.append(
            {
                "file": str(video),
                "video_seconds": round(duration, 2),
                "wall_seconds": round(wall, 2),
                "frames_decoded": payload.get("frames_decoded"),
                "frames_ocrd": frames_ocrd,
                "ms_per_ocrd_frame": round(wall / frames_ocrd * 1000, 1) if frames_ocrd else None,
                "realtime_factor": round(wall / duration, 2) if duration else None,
            }
        )
        print(
            f"{video.name:<24} {duration:6.1f}s video  {wall:7.1f}s wall  "
            f"{frames_ocrd:5d} frames  {wall / duration if duration else 0:5.2f}x realtime",
            flush=True,
        )

    seconds_per_minute = (total_wall / total_video_seconds * 60) if total_video_seconds else 0.0
    result = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "label": args.label,
        "machine": runtime.describe(),
        "config": {
            "engine": engine.name,
            "threads": threads,
            "video_mode": args.video_mode,
            "sample_fps": args.video_sample_fps,
            "max_height": args.video_max_height,
            "batch_size": args.ocr_batch_size,
            **engine.details(),
        },
        "totals": {
            "videos": len(per_video),
            "video_seconds": round(total_video_seconds, 1),
            "wall_seconds": round(total_wall, 1),
            "frames_ocrd": total_frames,
            "engine_load_seconds": round(load_seconds, 1),
            "seconds_per_video_minute": round(seconds_per_minute, 1),
            "realtime_factor": round(total_wall / total_video_seconds, 2)
            if total_video_seconds
            else None,
            "peak_rss_mb": round(peak_rss_mb(), 1),
        },
        "videos": per_video,
    }

    print()
    print(
        f"machine        : {platform.system()} {platform.machine()}, {runtime.describe()['cpu_count']} CPUs, {threads} thread(s)"
    )
    print(f"engine         : {engine.name}")
    print(f"video processed: {total_video_seconds:.1f}s across {len(per_video)} file(s)")
    print(f"wall clock     : {total_wall:.1f}s (engine load {load_seconds:.1f}s extra)")
    print(f"peak memory    : {result['totals']['peak_rss_mb']:.0f} MB")
    print()
    print(
        f">>> 1 minute of video costs {seconds_per_minute:.0f} s on this machine "
        f"({seconds_per_minute / 60:.2f} min per video-minute)"
    )

    # The numbers above are the deliverable; persisting them is a convenience.
    # A read-only or differently-owned bind mount must not fail the run.
    try:
        existing = []
        if args.output.exists():
            try:
                existing = json.loads(args.output.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = []
        existing.append(result)
        args.output.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nappended to {args.output}")
    except OSError as exc:
        print(
            f"\ncould not write {args.output} ({exc}); results above are complete", file=sys.stderr
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
