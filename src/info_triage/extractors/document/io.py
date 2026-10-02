"""Small atomic artifact writers shared by document extractors."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_bytes(value)
    temporary.replace(path)


def write_text(path: Path, value: str) -> None:
    write_bytes(path, value.encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def artifact_staging(target: Path) -> Iterator[Path]:
    """Yield a sibling staging directory and clean it unless it was published."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        yield staging
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def publish_directory(staging: Path, target: Path) -> None:
    """Atomically replace one artifact directory, restoring the old one on error."""
    backup = target.with_name(f".{target.name}.backup-{uuid.uuid4().hex}")
    if target.exists():
        target.replace(backup)
    try:
        staging.replace(target)
    except Exception:
        if backup.exists():
            backup.replace(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)
