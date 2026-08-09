from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class OCRLine:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class OCRFrame:
    time_seconds: float | None
    frame_number: int | None
    lines: list[OCRLine] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_seconds": self.time_seconds,
            "frame_number": self.frame_number,
            "lines": [line.to_dict() for line in self.lines],
        }


@dataclass(slots=True)
class TextSegment:
    text: str
    confidence: float
    first_seen_seconds: float | None
    last_seen_seconds: float | None
    bbox: tuple[float, float, float, float]
    observations: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

