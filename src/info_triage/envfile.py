"""The checkout's `.env`, read with the standard library alone.

One file holds every setting that differs between installations: the daemon's
secrets, where the server is, and what the laptop side should do. The daemon, the
capture client and the synchronizer all read it through here, so none of them
needs the others' dependencies installed to start.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """The checkout this package runs from: `src/info_triage/` is two levels down."""
    return Path(__file__).resolve().parents[2]


def load_env_file(path: Path | None = None) -> None:
    """Fill unset environment variables from a `KEY=VALUE` file, if present.

    Real environment variables always win, which is how one value is overridden
    for a single run. A missing file is not an error: inside the container the
    same values arrive through Compose's `env_file`.
    """
    try:
        lines = (path or project_root() / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))
