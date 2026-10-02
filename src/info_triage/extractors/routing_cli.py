"""Unified single-URL dispatcher for standalone extractors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .router import RouteResult, route_url


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="url-extract",
        description=(
            "Route and extract one URL. Put selected-extractor options after --, "
            "for example: url-extract URL -- --no-keep-raw"
        ),
    )
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=_path, default=_path("url_output"))
    parser.add_argument(
        "--show-route", action="store_true", help="print the selected route as JSON and exit"
    )
    return parser


def _split_args(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    index = argv.index("--")
    return argv[:index], argv[index + 1 :]


def _execute(route: RouteResult, output_dir: Path, forwarded: list[str]) -> int:
    if route.handler == "instagram":
        from info_triage.extractors.instagram.cli import main

        arguments = [route.routed_url, "--output-dir", str(output_dir / "instagram"), *forwarded]
    elif route.handler == "linkedin":
        from info_triage.extractors.linkedin.cli import main

        arguments = [route.routed_url, "--output-dir", str(output_dir / "linkedin"), *forwarded]
    elif route.handler == "youtube":
        from info_triage.extractors.youtube.cli import main

        arguments = [route.routed_url, "--output-dir", str(output_dir / "youtube"), *forwarded]
    elif route.handler == "medium":
        from info_triage.extractors.medium.cli import main

        arguments = [route.routed_url, "--output-dir", str(output_dir / "medium"), *forwarded]
    elif route.handler == "research":
        from info_triage.extractors.research.cli import main

        arguments = [route.routed_url, "--output-dir", str(output_dir), *forwarded]
    else:
        from info_triage.extractors.document.cli import main

        arguments = [route.routed_url, "--output-dir", str(output_dir), *forwarded]
    return main(arguments)


def main(argv: list[str] | None = None) -> int:
    router_args, forwarded = _split_args(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(router_args)
    try:
        route = route_url(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    if args.show_route:
        print(
            json.dumps(
                {
                    "handler": route.handler,
                    "identity": route.identity,
                    "source_url": route.source_url,
                    "routed_url": route.routed_url,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return _execute(route, args.output_dir, forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
