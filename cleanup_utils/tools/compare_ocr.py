#!/usr/bin/env python
"""Quality gates for the OCR pipeline, scored against already-stored results.

Three subcommands:

``sampling``  replays stored frame-level OCR at lower sample rates and reports
              how much substantial overlay text survives. Runs no model.
``images``    re-OCRs the carousel images with a candidate engine and compares
              against the stored (Surya) output. Decides the Linux image engine.
``bakeoff``   times candidate RapidOCR configurations on video frames and scores
              them against the stored per-video text.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rapidfuzz.fuzz import partial_ratio, ratio  # noqa: E402

from instagram_extractor.dedup import _key, merge_frames  # noqa: E402
from instagram_extractor.models import OCRFrame, OCRLine  # noqa: E402
from instagram_extractor.ocr import filter_thresholds  # noqa: E402

DEFAULT_ROOT = "instagram_output"


# --------------------------------------------------------------------------- io


def load_frames(payload: dict) -> list[OCRFrame]:
    return [
        OCRFrame(
            time_seconds=frame.get("time_seconds"),
            frame_number=frame.get("frame_number"),
            lines=[
                OCRLine(text=line["text"], confidence=float(line["confidence"]), bbox=tuple(line["bbox"]))
                for line in frame.get("lines", [])
            ],
        )
        for frame in payload.get("frames", [])
    ]


def useful_keys(segments, effective_fps: float, media_type: str = "video") -> set[str]:
    """Mirrors the llm_ready filter in ocr.py, parameterised by sampling rate."""
    minimum, short = filter_thresholds(effective_fps)
    keys = set()
    for segment in segments:
        visible = [c for c in segment.text if c.isalnum()]
        if not visible or segment.confidence < 0.45:
            continue
        if len(visible) <= 3 and segment.observations < short:
            continue
        if media_type == "video" and segment.observations < minimum:
            if not (len(visible) >= 12 and segment.confidence >= 0.9):
                continue
        key = _key(segment.text)
        if key:
            keys.add(key)
    return keys


def substantial(segments, min_chars: int = 8, min_seconds: float = 0.8) -> set[str]:
    """Text long enough and on screen long enough to be a real overlay."""
    keys = set()
    for segment in segments:
        alnum = sum(c.isalnum() for c in segment.text)
        duration = (segment.last_seen_seconds or 0) - (segment.first_seen_seconds or 0)
        if alnum >= min_chars and duration >= min_seconds:
            keys.add(_key(segment.text))
    return keys


# --------------------------------------------------------------------- sampling


def cmd_sampling(args) -> int:
    paths = sorted(glob.glob(f"{args.root}/*/ocr/*.ocr.json"))
    rates = [float(r) for r in args.rates.split(",")]
    totals: dict[float, list[int]] = {r: [0, 0, 0] for r in rates}

    header = f"{'video':<14}{'real':>5} | " + " | ".join(f"{f'{r:g}fps':>14}" for r in rates)
    print(header)
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("media_type") != "video":
            continue
        source_fps = float(payload.get("source_fps") or 30)
        frames = load_frames(payload)
        if not frames:
            continue
        full_segments = merge_frames(frames)
        real = substantial(full_segments) & useful_keys(full_segments, source_fps)
        if not real:
            continue
        cells = []
        for rate in rates:
            stride = max(1, int(round(source_fps / rate)))
            sampled = frames[::stride]
            got = useful_keys(merge_frames(sampled), source_fps / stride)
            hit = len(real & got)
            cells.append(f"{len(sampled):4d}f {hit / len(real) * 100:5.1f}%")
            totals[rate][0] += hit
            totals[rate][1] += len(real)
            totals[rate][2] += len(sampled)
        print(f"{Path(path).parts[-3][:12]:<14}{len(real):>5} | " + " | ".join(cells))

    print()
    for rate in rates:
        hit, total, nframes = totals[rate]
        if total:
            print(f"{rate:>5g} fps: {nframes:6d} frames OCR'd   substantial-text recall {hit / total * 100:5.1f}%")
    return 0


# ----------------------------------------------------------------------- images


def cmd_images(args) -> int:
    from PIL import Image

    from instagram_extractor.engines import RapidOCREngine

    engine = RapidOCREngine(
        scripts=tuple(args.scripts.split(",")),
        threads=args.threads,
        detector_side=args.detector_side,
    )
    paths = sorted(glob.glob(f"{args.root}/*/ocr/*.ocr.json"))
    tot_ref = tot_hit = tot_new = tot_fuzzy = 0
    tot_covered = tot_ref_chars = 0
    elapsed = 0.0
    count = 0
    print(f"{'image':<34}{'surya':>7}{'rapid':>7}{'exact':>7}{'fuzzy':>7}{'new':>6}{'ms':>7}")
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("media_type") != "image":
            continue
        media = Path(path).parents[1] / "media" / payload["source_file"]
        if not media.exists():
            continue
        reference = {_key(s["text"]) for s in payload.get("llm_ready_segments", []) if _key(s["text"])}
        with Image.open(media) as image:
            start = time.time()
            lines = engine.recognize(image.convert("RGB"))
            took = (time.time() - start) * 1000
        got = {_key(line.text) for line in lines if _key(line.text) and line.confidence >= 0.45}
        # Exact key equality treats any OCR spelling wobble as a miss, so also
        # score a fuzzy match at the same threshold the deduplicator uses.
        fuzzy = sum(
            1
            for want in reference
            if want in got or any(ratio(want, other) >= 88 for other in got)
        )
        # The engines segment lines differently: Surya returns a whole paragraph
        # as one line where PP-OCR returns several. Line-set metrics score that
        # as a miss even when every character was read. Coverage compares each
        # reference line against the concatenation of everything found.
        blob = "".join(sorted(got))
        covered = sum(len(want) for want in reference if partial_ratio(want, blob) >= 90)
        ref_chars = sum(len(want) for want in reference)
        tot_covered += covered
        tot_ref_chars += ref_chars
        hit = len(reference & got)
        tot_ref += len(reference)
        tot_hit += hit
        tot_fuzzy += fuzzy
        tot_new += len(got - reference)
        elapsed += took
        count += 1
        if args.verbose and reference:
            missed = sorted(w for w in reference if not any(ratio(w, o) >= 88 for o in got))
            if missed:
                print(f"    missed: {missed[:6]}")
        print(
            f"{(Path(path).parts[-3] + '/' + payload['source_file'])[:33]:<34}"
            f"{len(reference):>7}{len(got):>7}{hit:>7}{fuzzy:>7}{len(got - reference):>6}{took:>7.0f}"
        )
    print()
    if tot_ref:
        print(
            f"TOTAL over {count} images: exact {tot_hit}/{tot_ref} = {tot_hit / tot_ref * 100:.1f}%, "
            f"fuzzy(>=88) {tot_fuzzy}/{tot_ref} = {tot_fuzzy / tot_ref * 100:.1f}% of Surya lines; "
            f"character coverage {tot_covered / max(1, tot_ref_chars) * 100:.1f}%; "
            f"{tot_new} lines Surya did not report; {elapsed / max(1, count):.0f} ms/image"
        )
    return 0


# ---------------------------------------------------------------------- bakeoff


def sample_frames(path: str, fps_target: float, limit: int, max_height: int):
    import cv2

    capture = cv2.VideoCapture(path)
    source_fps = capture.get(cv2.CAP_PROP_FPS) or 30
    stride = max(1, round(source_fps / fps_target))
    out = []
    index = 0
    while len(out) < limit:
        if not capture.grab():
            break
        if index % stride == 0:
            ok, frame = capture.retrieve()
            if ok:
                if max_height and frame.shape[0] > max_height:
                    scale = max_height / frame.shape[0]
                    frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                out.append(frame)
        index += 1
    capture.release()
    return out


def cmd_bakeoff(args) -> int:
    from PIL import Image

    from instagram_extractor.engines import RapidOCREngine

    videos = sorted(glob.glob(f"{args.root}/*/media/*.mp4"))[: args.videos]
    reference: dict[str, set[str]] = {}
    for video in videos:
        result = Path(video).parents[1] / "ocr" / (Path(video).stem + ".ocr.json")
        if not result.exists():
            continue
        payload = json.loads(result.read_text(encoding="utf-8"))
        segments = merge_frames(load_frames(payload))
        reference[video] = substantial(segments) & useful_keys(segments, float(payload.get("source_fps") or 30))

    configs = []
    for script in args.scripts.split(","):
        for side in [int(s) for s in args.detector_sides.split(",")]:
            for height in [int(h) for h in args.heights.split(",")]:
                configs.append((script, side, height))

    print(f"{'config':<40}{'ms/frame':>10}{'recall':>9}{'found':>8}{'extra':>8}")
    for script, side, height in configs:
        engine = RapidOCREngine(scripts=(script,), threads=args.threads, detector_side=side)
        total_ms = 0.0
        frames_done = 0
        hit = ref_total = extra = 0
        for video in videos:
            if video not in reference or not reference[video]:
                continue
            frames = sample_frames(video, args.fps, args.limit, height)
            found: set[str] = set()
            for frame in frames:
                image = Image.fromarray(frame[:, :, ::-1])
                start = time.time()
                lines = engine.recognize(image)
                total_ms += (time.time() - start) * 1000
                frames_done += 1
                for line in lines:
                    if line.confidence >= 0.45 and _key(line.text):
                        found.add(_key(line.text))
            want = reference[video]
            hit += len(want & found)
            ref_total += len(want)
            extra += len(found - want)
        label = f"{script}, det={side}, h={height}"
        recall = hit / ref_total * 100 if ref_total else 0.0
        print(
            f"{label:<40}{total_ms / max(1, frames_done):>10.0f}{recall:>8.1f}%{hit:>4}/{ref_total:<3}{extra:>8}"
        )
        sys.stdout.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sampling", help="recall vs sample rate, replayed from stored frames")
    p.add_argument("--rates", default="6,4,3,2,1")
    p.set_defaults(func=cmd_sampling)

    p = sub.add_parser("images", help="RapidOCR vs the stored Surya image output")
    p.add_argument("--scripts", default="cyrillic")
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--detector-side", type=int, default=736)
    p.add_argument("--verbose", action="store_true", help="print lines RapidOCR missed")
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("bakeoff", help="time and score candidate RapidOCR configurations")
    p.add_argument("--scripts", default="cyrillic,eslav,latin")
    p.add_argument("--detector-sides", default="640,736,960")
    p.add_argument("--heights", default="640,0")
    p.add_argument("--fps", type=float, default=3.0)
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--videos", type=int, default=4)
    p.add_argument("--threads", type=int, default=None)
    p.set_defaults(func=cmd_bakeoff)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
