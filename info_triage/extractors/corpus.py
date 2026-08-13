"""Explicit live runner for the URL extraction acceptance corpus."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from info_triage.extractors.document.io import write_json
from info_triage.extractors.router import route_url


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _urls(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _output_path(stdout: str) -> Path | None:
    for line in reversed(stdout.splitlines()):
        if " -> " in line:
            return Path(line.rsplit(" -> ", 1)[1].strip())
    return None


def _status(path: Path | None, returncode: int, stderr: str) -> dict[str, Any]:
    if path is not None:
        status_path = path / "status.json"
        try:
            value = json.loads(status_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                if "status" not in value:
                    download = value.get("download")
                    if download == "complete" or returncode == 0:
                        value["status"] = "complete"
                    elif download in {"preview", "partial"}:
                        value["status"] = "partial"
                    else:
                        value["status"] = "failed"
                return value
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "status": "complete" if returncode == 0 else "failed",
        "reason": None if returncode == 0 else "runner-error",
        "error": stderr.strip() or None,
    }


def build_parser() -> argparse.ArgumentParser:
    root = Path("tests/fixtures/url_extraction")
    parser = argparse.ArgumentParser(description="Run the explicit live URL extraction corpus.")
    parser.add_argument("--html-manifest", type=_path, default=_path(root / "html.txt"))
    parser.add_argument("--pdf-manifest", type=_path, default=_path(root / "pdf.txt"))
    parser.add_argument("--research-manifest", type=_path, default=_path(root / "research.txt"))
    parser.add_argument("--output-dir", type=_path, default=_path("url_output"))
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--no-keep-raw", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.request_delay < 0:
        parser.error("--request-delay must be non-negative")
    groups = {
        "html": _urls(args.html_manifest),
        "pdf": _urls(args.pdf_manifest),
        "research": _urls(args.research_manifest),
    }
    records: list[dict[str, Any]] = []
    total = sum(map(len, groups.values()))
    sequence = 0
    for group, urls in groups.items():
        for url in urls:
            sequence += 1
            if sequence > 1 and args.request_delay:
                time.sleep(args.request_delay)
            try:
                route = route_url(url, resolve_redirectors=False)
                handler = route.handler
                provider = getattr(route.reference, "provider", None)
            except ValueError as exc:
                records.append(
                    {
                        "manifest_group": group,
                        "url": url,
                        "status": "failed",
                        "reason": "routing-error",
                        "error": str(exc),
                    }
                )
                print(f"[{sequence}/{total}] routing failed: {url}", file=sys.stderr)
                continue
            command = [
                sys.executable,
                "-m",
                "info_triage.extractors.routing_cli",
                "--output-dir",
                str(args.output_dir),
                url,
            ]
            if args.no_keep_raw and handler in {"document", "research"}:
                command.extend(["--", "--no-keep-raw"])
            print(f"[{sequence}/{total}] {handler}: {url}", flush=True)
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            output_path = _output_path(completed.stdout)
            status = _status(output_path, completed.returncode, completed.stderr)
            records.append(
                {
                    "manifest_group": group,
                    "url": url,
                    "handler": handler,
                    "provider": provider,
                    "output": str(output_path) if output_path else None,
                    "returncode": completed.returncode,
                    "status": status.get("status", "failed"),
                    "reason": status.get("reason"),
                    "error": status.get("error"),
                }
            )

    outcomes = Counter(str(record["status"]) for record in records)
    by_route: dict[str, dict[str, int]] = {}
    for record in records:
        key = str(record.get("provider") or record.get("handler") or "routing")
        counts = by_route.setdefault(key, {})
        outcome = str(record["status"])
        counts[outcome] = counts.get(outcome, 0) + 1
    reasons = Counter(str(record["reason"]) for record in records if record.get("reason"))
    summary = {
        "schema_version": 1,
        "attempted": len(records),
        "manifest_counts": {name: len(urls) for name, urls in groups.items()},
        "outcomes": dict(sorted(outcomes.items())),
        "by_route": by_route,
        "failure_reasons": dict(sorted(reasons.items())),
        "items": records,
    }
    write_json(args.output_dir / "summary.json", summary)
    print(f"summary -> {args.output_dir / 'summary.json'}")
    return 0 if outcomes.get("failed", 0) == 0 and outcomes.get("partial", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
