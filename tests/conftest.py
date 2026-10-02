"""Shared construction helpers for the capture and processing suites.

`store.capture` and `ProcessingJob` carry a route now, and almost every test
builds one or both. Routing those constructions through here keeps the next
signature change to one edit rather than eighty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from info_triage.models import CapturedItem, ProcessingJob
from info_triage.storage import CaptureStore

DEFAULT_ROUTE = "info"
#: The routes the suites capture into. The daemon takes its routes from
#: configuration, so the tests have to name some.
ROUTES = ("info", "job", "clip", "lang")


def make_store(data_dir: Path) -> CaptureStore:
    return CaptureStore(data_dir, ROUTES)


@pytest.fixture(autouse=True)
def no_checkout_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own `.env` out of the test process.

    The command-line entry points load it, and it holds real tokens and this
    machine's server address.
    """
    monkeypatch.setattr("info_triage.sync.load_env_file", lambda: None)
    monkeypatch.setattr("info_triage.capture_cli.load_env_file", lambda: None)


@pytest.fixture
def store(tmp_path: Path) -> CaptureStore:
    return make_store(tmp_path)


def capture(
    store: CaptureStore,
    chat_id: int,
    message_id: int,
    content: str,
    *,
    origin_route: str = DEFAULT_ROUTE,
    **kwargs: Any,
) -> CapturedItem:
    """Capture into a route, defaulting to the one every old test assumed."""
    return store.capture(origin_route, chat_id, message_id, content, **kwargs)


def job_for(item: CapturedItem, **overrides: Any) -> ProcessingJob:
    """The processing job a coordinator would build for a captured item."""
    fields = {
        "origin_route": item.origin_route,
        "chat_id": item.chat_id,
        "message_id": item.message_id,
        "route": item.route,
        "local_id": item.local_id,
        "revision": item.revision,
        "path": item.path,
    }
    fields.update(overrides)
    return ProcessingJob(**fields)
