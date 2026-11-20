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
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from .envfile import load_env_file

DEFAULT_URL = "http://localhost:8000"
TOKEN_VARIABLE = "INFO_TRIAGE_CAPTURE_TOKEN"
URL_VARIABLE = "INFO_TRIAGE_CAPTURE_URL"
# The laptop's copy of the inbox, as `sync.sh` reads it.
INBOX_VARIABLE = "INFO_TRIAGE_INBOX"
# The two index.md frontmatter fields that say an item's own source was not
# retrieved. Plain `key: value` lines; the frontmatter is the item contract.
FRONTMATTER_FIELD = re.compile(r"^(origin|extraction):[ \t]*(\S+)[ \t]*$", re.MULTILINE)
UNRETRIEVED = ("failed", "blocked")


def unretrieved_handles(inbox: Path, handler: str) -> list[str]:
    """Handles of the delivered items whose `handler` source came back empty."""
    handles = []
    for index in sorted(inbox.glob("*/*/index.md")):
        text = index.read_text(encoding="utf-8", errors="replace")
        end = text.find("\n---", 4)
        if not text.startswith("---\n") or end < 0:
            continue
        fields = dict(FRONTMATTER_FIELD.findall(text[4:end]))
        if fields.get("origin") == handler and fields.get("extraction") in UNRETRIEVED:
            handles.append(f"{index.parent.parent.name}/{index.parent.name}")
    return handles


def _post(base_url: str, path: str, payload: dict, token: str) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


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
    parser.add_argument(
        "text",
        nargs="*",
        help="Capture text, read from stdin when omitted; with --reprocess, item handles",
    )
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
        "--reprocess",
        action="store_true",
        help="Run the pipeline again over the items whose handles are given, keeping "
        "their captured content. For enrichment that failed while a source refused.",
    )
    parser.add_argument(
        "--reprocess-failed",
        metavar="HANDLER",
        help="Reprocess every item in the local inbox whose HANDLER source (e.g. "
        f"instagram) came back failed or blocked. The inbox is ${INBOX_VARIABLE} "
        "or ~/info-triage-inbox.",
    )
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

    if arguments.reprocess or arguments.reprocess_failed:
        return _reprocess(arguments, token)

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

    try:
        item = _post(arguments.url, "/capture", payload, token)
        # The server's route, not the requested one: replacing an item into
        # another route renumbers it, and this line is the handle to reuse.
        print(f"{item['route']}/{item['id']}")
    except urllib.error.HTTPError as error:
        print(f"{error.code} {error.read().decode('utf-8', 'replace').strip()}", file=sys.stderr)
        return 1
    except OSError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def _reprocess(arguments: argparse.Namespace, token: str) -> int:
    handles = list(arguments.text)
    if arguments.reprocess_failed:
        inbox = Path(
            os.environ.get(INBOX_VARIABLE) or Path.home() / "info-triage-inbox"
        ).expanduser()
        handles += unretrieved_handles(inbox, arguments.reprocess_failed)
    if not handles:
        print("nothing to reprocess", file=sys.stderr)
        return 0
    try:
        answer = _post(arguments.url, "/reprocess", {"ids": handles}, token)
    except urllib.error.HTTPError as error:
        print(f"{error.code} {error.read().decode('utf-8', 'replace').strip()}", file=sys.stderr)
        return 1
    except OSError as error:
        print(error, file=sys.stderr)
        return 1
    failures = 0
    for result in answer.get("results", []):
        if result.get("status") == 200:
            print(f"{result['id']} revision {result['revision']}")
        else:
            failures += 1
            print(f"{result['status']} {result['id']}: {result.get('error')}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
