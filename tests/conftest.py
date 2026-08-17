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


@pytest.fixture
def store(tmp_path: Path) -> CaptureStore:
    return CaptureStore(tmp_path)


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
        "category": item.category,
        "path": item.path,
    }
    fields.update(overrides)
    return ProcessingJob(**fields)
