"""Capture from anywhere that is not Telegram: `info-triage-capture`.

Deliberately standard-library only. This runs on a laptop, from a shell profile
or a one-line script behind a browser button, and should not need the daemon's
dependencies installed to work.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from .config import ROUTE_NAMES

DEFAULT_URL = "http://localhost:8000"
TOKEN_VARIABLE = "INFO_TRIAGE_CAPTURE_TOKEN"
URL_VARIABLE = "INFO_TRIAGE_CAPTURE_URL"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file(path: Path) -> None:
    """Fill unset environment variables from a `KEY=VALUE` file, if present.

    Real environment variables always win; this only covers what a checkout's
    own .env already provides, so a laptop running from source needs no shell
    profile changes. Deliberately not python-dotenv: that would pull in the
    daemon's dependencies for what is otherwise a stdlib-only script.
    """
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def resolve_route(route: str | None, item_id: str | None) -> str:
    """Decide which route a request names, given what was and was not asked for.

    A replacement that did not ask to move the item must not move it: a --route
    defaulting to info would quietly re-file every clip item it corrected.
    """
    if route is not None:
        return route
    if item_id:
        return item_id.split("/")[0]
    return "info"


def build_payload(
    route: str,
    text: str,
    files: Sequence[Path],
    source: str,
    captured_at: str | None,
    item_id: str | None = None,
) -> dict:
    payload = {
        "route": route,
        "source": source,
        "text": text,
        "files": [
            {
                "name": path.name,
                "mime_type": mimetypes.guess_type(path.name)[0],
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
            for path in files
        ],
    }
    if captured_at is not None:
        payload["captured_at"] = captured_at
    if item_id is not None:
        payload["id"] = item_id
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    _load_env_file(ENV_FILE)
    parser = argparse.ArgumentParser(
        prog="info-triage-capture",
        description="Send one capture to the Info Triage daemon over HTTP.",
    )
    parser.add_argument("text", nargs="*", help="Capture text; read from stdin when omitted")
    parser.add_argument(
        "--route",
        default=None,
        choices=ROUTE_NAMES,
        help="Which pipeline and inbox the item belongs to (default: info, or --id's route)",
    )
    parser.add_argument(
        "--id",
        dest="item_id",
        help="Replace the item with this handle, as printed by an earlier capture "
        "(<route>/<name>). Everything omitted here is dropped, attachments included.",
    )
    parser.add_argument(
        "--file",
        action="append",
        type=Path,
        default=[],
        dest="files",
        help="Attach a file. Repeatable.",
    )
    parser.add_argument("--source", default="cli", help="Which client this is (default: cli)")
    parser.add_argument("--captured-at", help="ISO-8601 timestamp with an offset, if not right now")
    parser.add_argument(
        "--url",
        default=os.environ.get(URL_VARIABLE, DEFAULT_URL),
        help=f"Daemon base URL (default: ${URL_VARIABLE} or {DEFAULT_URL})",
    )
    arguments = parser.parse_args(argv)

    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    if not token:
        print(f"{TOKEN_VARIABLE} is not set", file=sys.stderr)
        return 2

    route = resolve_route(arguments.route, arguments.item_id)
    text = " ".join(arguments.text) if arguments.text else sys.stdin.read()
    try:
        payload = build_payload(
            route,
            text.strip(),
            arguments.files,
            arguments.source,
            arguments.captured_at,
            arguments.item_id,
        )
    except OSError as error:
        print(error, file=sys.stderr)
        return 1

    request = urllib.request.Request(
        f"{arguments.url.rstrip('/')}/capture",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            # The server's route, not the requested one: replacing an item into
            # another route renumbers it, and this line is the handle to reuse.
            item = json.load(response)
            print(f"{item['route']}/{item['id']}")
    except urllib.error.HTTPError as error:
        print(f"{error.code} {error.read().decode('utf-8', 'replace').strip()}", file=sys.stderr)
        return 1
    except OSError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
