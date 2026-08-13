"""Data structures and stable errors for linked-document extraction."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ExtractionError(RuntimeError):
    message: str
    reason: str = "extraction-failed"
    url: str | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class FetchResult:
    requested_url: str
    final_url: str
    content_type: str
    kind: str
    body: bytes
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    method: str = "http"
