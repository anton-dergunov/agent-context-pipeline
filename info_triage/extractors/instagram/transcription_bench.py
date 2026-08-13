"""Reproducible multilingual Instagram faster-whisper benchmark.

This command intentionally stops at model comparison.  It does not choose a
production default or modify the extractor/Docker runtime.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import resource
import subprocess
import sys
import tarfile
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .transcription import download_faster_whisper_model
from .transcription_metrics import score_transcript, tokenize
from .urls import load_inputs

MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
FLEURS_ARCHIVE = (
    "https://huggingface.co/datasets/google/fleurs/resolve/main/data/{config}/audio/test.tar.gz"
)
# These downloaded examples are dance/music posts.  They are rerun without
# VAD to measure whether speech filtering prevents spurious text.
DEFAULT_NEGATIVE_SHORTCODES = {"C8aGZQwuB1d", "DK4OKPVOHOg", "DS2Ag0GDBv2"}


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def load_fleurs_manifest(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    required = {"config", "sample_id", "filename", "language", "reference"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"invalid FLEURS manifest: expected columns {sorted(required)}")
    return rows


def cache_fleurs_samples(manifest: Path, cache_dir: Path) -> list[dict[str, Any]]:
    """Stream each language archive once and retain only selected WAV files."""
    import requests

    rows = load_fleurs_manifest(manifest)
    samples: list[dict[str, Any]] = []
    by_config: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_config[row["config"]].append(row)
    for config, config_rows in by_config.items():
        missing = [
            row
            for row in config_rows
            if not (cache_dir / config / row["filename"]).exists()
            or (cache_dir / config / row["filename"]).stat().st_size == 0
        ]
        if missing:
            archive_dir = cache_dir.parent / "fleurs-downloads"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive = archive_dir / f"{config}-test.tar.gz"
            temporary = archive.with_suffix(archive.suffix + ".part")
            print(f"Downloading FLEURS {config} test archive …", flush=True)
            downloaded = temporary.stat().st_size if temporary.exists() else 0
            headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
            with requests.get(
                FLEURS_ARCHIVE.format(config=config),
                headers=headers,
                stream=True,
                timeout=(30, 300),
            ) as response:
                response.raise_for_status()
                mode = "ab" if downloaded and response.status_code == 206 else "wb"
                with temporary.open(mode) as handle:
                    for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            temporary.replace(archive)
            wanted = {row["filename"] for row in missing}
            found: set[str] = set()
            with tarfile.open(archive, "r:gz") as bundle:
                for member in bundle:
                    filename = Path(member.name).name
                    if filename not in wanted or not member.isfile():
                        continue
                    source = bundle.extractfile(member)
                    if source is None:
                        continue
                    destination = cache_dir / config / filename
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read())
                    found.add(filename)
            archive.unlink()
            absent = wanted - found
            if absent:
                raise RuntimeError(f"FLEURS audio not found in {config} archive: {sorted(absent)}")
    for row in rows:
        destination = cache_dir / row["config"] / row["filename"]
        samples.append(
            {
                "key": f"fleurs/{row['config']}/{row['filename']}",
                "kind": "controlled",
                "path": str(destination),
                "expected_language": row["language"],
                "reference": row["reference"],
                "negative": False,
            }
        )
    return samples


def discover_instagram_samples(input_file: Path, output_dir: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for _, shortcode in load_inputs([], input_file):
        media_dir = output_dir / shortcode / "media"
        for path in sorted(media_dir.glob("*")):
            if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
                samples.append(
                    {
                        "key": f"instagram/{shortcode}/{path.name}",
                        "kind": "instagram",
                        "path": str(path.resolve()),
                        "expected_language": None,
                        "reference": None,
                        "negative": shortcode in DEFAULT_NEGATIVE_SHORTCODES,
                    }
                )
    return samples


def telegram_samples(paths: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "key": f"telegram/{index:02d}/{path.name}",
            "kind": "telegram",
            "path": str(path),
            "expected_language": None,
            "reference": None,
            "negative": False,
        }
        for index, path in enumerate(paths, 1)
    ]


def _download_model(model: str, model_cache: Path) -> Path:
    return download_faster_whisper_model(model, model_cache)


def worker(spec_path: Path, output_path: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    model_name = spec["model"]
    started = time.perf_counter()
    model_path = _download_model(model_name, Path(spec["model_cache"]))

    from .transcription import FasterWhisperTranscriber

    transcriber = FasterWhisperTranscriber(model_path, threads=int(spec["threads"]))
    results: list[dict[str, Any]] = []
    vad_off: list[dict[str, Any]] = []
    for sample in spec["samples"]:
        print(f"[{model_name}] {sample['key']}", flush=True)
        result = transcriber.transcribe(
            Path(sample["path"]),
            language=None,
            vad=True,
            beam_size=int(spec["beam_size"]),
            keep_segments=True,
        ).to_dict()
        result.update(
            {
                key: sample.get(key)
                for key in ("key", "kind", "expected_language", "reference", "negative")
            }
        )
        results.append(result)
        if sample.get("negative"):
            unfiltered = transcriber.transcribe(
                Path(sample["path"]),
                language=None,
                vad=False,
                beam_size=int(spec["beam_size"]),
                keep_segments=True,
            ).to_dict()
            unfiltered.update({"key": sample["key"], "kind": sample["kind"]})
            vad_off.append(unfiltered)

    payload = {
        "schema_version": 1,
        "model": model_name,
        "config": {
            "device": "cpu",
            "compute_type": "int8",
            "threads": int(spec["threads"]),
            "beam_size": int(spec["beam_size"]),
            "vad": True,
        },
        "resources": {
            "model_size_mb": round(_directory_size(model_path) / 1_000_000, 1),
            "model_load_seconds": round(transcriber.load_seconds, 3),
            "total_wall_seconds": round(time.perf_counter() - started, 3),
            "total_inference_seconds": round(sum(item["inference_seconds"] for item in results), 3),
            "vad_off_inference_seconds": round(
                sum(item["inference_seconds"] for item in vad_off), 3
            ),
            "audio_seconds": round(
                sum(float(item.get("duration_seconds") or 0.0) for item in results), 3
            ),
            "peak_rss_mb": round(_peak_rss_mb(), 1),
        },
        "results": results,
        "vad_off_results": vad_off,
    }
    _json_write(output_path, payload)
    return 0


def _average(values: list[float]) -> float | None:
    return mean(values) if values else None


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _safe_limit_mb(peak_mb: float) -> int:
    return max(512, int(math.ceil((peak_mb * 1.5) / 256) * 256))


def build_report(runs: list[dict[str, Any]]) -> str:
    by_model = {run["model"]: run for run in runs}
    large_results = {
        result["key"]: result for result in by_model.get("large-v3", {}).get("results", [])
    }
    lines = [
        "# Multilingual transcription model findings",
        "",
        "All models used CPU int8 inference, beam size 5, VAD enabled, and one thread. "
        "FLEURS metrics use human reference transcripts; large-v3 agreement is a model proxy, not ground truth.",
        "",
        "## Resources",
        "",
        "| Model | Disk | Peak RSS | Safe Docker allowance | Load | Primary inference | Real-time factor | VAD-off extra | Wall |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in MODELS:
        run = by_model.get(model)
        if not run:
            continue
        resource_data = run["resources"]
        peak = float(resource_data["peak_rss_mb"])
        audio_seconds = float(resource_data["audio_seconds"])
        realtime = (
            float(resource_data["total_inference_seconds"]) / audio_seconds
            if audio_seconds
            else 0.0
        )
        lines.append(
            f"| {model} | {resource_data['model_size_mb']:.0f} MB | {peak:.0f} MB | "
            f"{_safe_limit_mb(peak)} MB | {resource_data['model_load_seconds']:.1f}s | "
            f"{resource_data['total_inference_seconds']:.1f}s | {realtime:.2f}x | "
            f"{resource_data['vad_off_inference_seconds']:.1f}s | {resource_data['total_wall_seconds']:.1f}s |"
        )

    lines.extend(
        [
            "",
            "## Controlled language matrix",
            "",
            "Reference P/R/F1 and Jaccard compare with human FLEURS text. Large F1/J compare with large-v3 output.",
            "",
            "| Language | Model | Ref P | Ref R | Ref F1 | Ref J | Large F1 | Large J | Lang accuracy | Mean probability |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    language_names = {"en": "English", "es": "Spanish", "ru": "Russian", "zh": "Mandarin"}
    for language in ("en", "es", "ru", "zh"):
        for model in MODELS:
            run = by_model.get(model)
            if not run:
                continue
            controlled = [
                item
                for item in run["results"]
                if item.get("kind") == "controlled" and item.get("expected_language") == language
            ]
            reference_scores = [
                score_transcript(item["reference"], item["text"], language) for item in controlled
            ]
            agreement_scores = []
            for item in controlled:
                baseline = large_results.get(item["key"])
                if baseline:
                    agreement_scores.append(
                        score_transcript(baseline["text"], item["text"], language)
                    )
            correct = [1.0 if item.get("language") == language else 0.0 for item in controlled]
            probabilities = [
                float(item["language_probability"])
                for item in controlled
                if item.get("language_probability") is not None
            ]
            lines.append(
                f"| {language_names[language]} | {model} | "
                f"{_percent(_average([score.rouge_l_precision for score in reference_scores]))} | "
                f"{_percent(_average([score.rouge_l_recall for score in reference_scores]))} | "
                f"{_percent(_average([score.rouge_l_f1 for score in reference_scores]))} | "
                f"{_percent(_average([score.jaccard for score in reference_scores]))} | "
                f"{_percent(_average([score.rouge_l_f1 for score in agreement_scores]))} | "
                f"{_percent(_average([score.jaccard for score in agreement_scores]))} | "
                f"{_percent(_average(correct))} | {_percent(_average(probabilities))} |"
            )

    lines.extend(
        [
            "",
            "## VAD effect on expected music-only posts",
            "",
            "Token counts use the language detected by each run. Lower is better when there is no speech.",
            "",
            "| Sample | Model | VAD on words | VAD off words | VAD on text | VAD off text |",
            "|---|---|---:|---:|---|---|",
        ]
    )
    for model in MODELS:
        run = by_model.get(model)
        if not run:
            continue
        primary = {item["key"]: item for item in run["results"]}
        for off in run.get("vad_off_results", []):
            on = primary[off["key"]]
            language = on.get("language") or off.get("language") or "en"
            on_words = len(tokenize(on.get("text", ""), language))
            off_words = len(tokenize(off.get("text", ""), language))
            on_text = on.get("text", "").replace("|", "\\|")
            off_text = off.get("text", "").replace("|", "\\|")
            lines.append(
                f"| {off['key']} | {model} | {on_words} | {off_words} | {on_text} | {off_text} |"
            )

    real_keys: list[str] = []
    for run in runs:
        for item in run["results"]:
            if item.get("kind") in {"instagram", "telegram"} and item["key"] not in real_keys:
                real_keys.append(item["key"])
    lines.extend(["", "## Real-world side-by-side transcripts", ""])
    for key in real_keys:
        lines.extend([f"### {key}", ""])
        for model in MODELS:
            run = by_model.get(model)
            if not run:
                continue
            item = next((value for value in run["results"] if value["key"] == key), None)
            if not item:
                continue
            probability = item.get("language_probability")
            probability_text = "—" if probability is None else f"{float(probability):.3f}"
            lines.extend(
                [
                    f"**{model}** — status `{item['status']}`, language `{item.get('language')}` ({probability_text})",
                    "",
                    item.get("text") or "_(empty)_",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark multilingual faster-whisper models without selecting a default"
    )
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument(
        "--input-file",
        type=_path,
        default=_path("benchmarks/data/instagram_urls.txt"),
    )
    parser.add_argument("--instagram-output", type=_path, default=_path("instagram_output"))
    parser.add_argument(
        "--fleurs-manifest",
        type=_path,
        default=_path("benchmarks/data/transcription_benchmark_samples.tsv"),
    )
    parser.add_argument("--work-dir", type=_path, default=_path(".bench_transcription"))
    parser.add_argument("--model-cache", type=_path, default=_path(".whisper_models"))
    parser.add_argument("--telegram-audio", action="append", type=_path, default=[])
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--report", type=_path, default=_path(".bench_transcription/report.md"))
    parser.add_argument("--worker-spec", type=_path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=_path, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.worker_spec:
        if not args.worker_output:
            raise SystemExit("--worker-output is required with --worker-spec")
        return worker(args.worker_spec, args.worker_output)
    if args.threads < 1 or args.beam_size < 1:
        raise SystemExit("--threads and --beam-size must be positive")

    fleurs = cache_fleurs_samples(args.fleurs_manifest, args.work_dir / "fleurs")
    instagram = discover_instagram_samples(args.input_file, args.instagram_output)
    telegram = telegram_samples(args.telegram_audio)
    samples = fleurs + instagram + telegram
    if not instagram:
        raise SystemExit(f"no downloaded Instagram videos found under {args.instagram_output}")
    for sample in samples:
        if not Path(sample["path"]).exists():
            raise SystemExit(f"missing benchmark input: {sample['path']}")

    runs = []
    for model in args.models:
        spec_path = args.work_dir / "specs" / f"{model}.json"
        result_path = args.work_dir / "results" / f"{model}.json"
        log_path = args.work_dir / "logs" / f"{model}.log"
        _json_write(
            spec_path,
            {
                "model": model,
                "model_cache": str(args.model_cache),
                "threads": args.threads,
                "beam_size": args.beam_size,
                "samples": samples,
            },
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Benchmarking {model} ({len(samples)} primary samples) …", flush=True)
        environment = os.environ.copy()
        for name in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        ):
            environment[name] = str(args.threads)
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "info_triage.extractors.instagram.transcription_bench",
                    "--worker-spec",
                    str(spec_path),
                    "--worker-output",
                    str(result_path),
                ],
                cwd=Path.cwd(),
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if completed.returncode:
            print(log_path.read_text(encoding="utf-8"), file=sys.stderr)
            raise SystemExit(f"{model} benchmark failed; see {log_path}")
        runs.append(json.loads(result_path.read_text(encoding="utf-8")))
        resources = runs[-1]["resources"]
        print(
            f"  {resources['total_inference_seconds']:.1f}s inference, "
            f"{resources['peak_rss_mb']:.0f} MB peak RSS",
            flush=True,
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(build_report(runs), encoding="utf-8")
    print(f"Report: {args.report}")
    print("No production model was selected or baked into Docker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
