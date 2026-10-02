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

from .envfile import load_env_file

DEFAULT_URL = "http://localhost:8000"
TOKEN_VARIABLE = "INFO_TRIAGE_CAPTURE_TOKEN"
URL_VARIABLE = "INFO_TRIAGE_CAPTURE_URL"


def build_payload(
    route: str | None,
    text: str,
    files: Sequence[Path],
    source: str,
    captured_at: str | None,
    item_id: str | None = None,
) -> dict:
    payload = {
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
    # Sent only when asked for. Left out, the server keeps a replaced item where
    # it is and files a new one under its default route, so this client never has
    # to know which routes an installation defines.
    if route is not None:
        payload["route"] = route
    if captured_at is not None:
        payload["captured_at"] = captured_at
    if item_id is not None:
        payload["id"] = item_id
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(
        prog="info-triage-capture",
        description="Send one capture to the Info Triage daemon over HTTP.",
    )
    parser.add_argument("text", nargs="*", help="Capture text; read from stdin when omitted")
    parser.add_argument(
        "--route",
        default=None,
        help="Which pipeline and inbox the item belongs to (default: the server's first "
        "route, or --id's route)",
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

    text = " ".join(arguments.text) if arguments.text else sys.stdin.read()
    try:
        payload = build_payload(
            arguments.route,
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
