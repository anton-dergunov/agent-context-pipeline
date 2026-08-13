"""Small shared data objects used by capture and processing."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CATEGORIES = ("ML", "Career", "Life", "Other")


@dataclass(frozen=True)
class AttachmentSpec:
    kind: str
    file_id: str
    file_unique_id: str
    file_size: int | None
    mime_type: str | None
    original_name: str | None
    extension: str
    source_message_id: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file_id": self.file_id,
            "file_unique_id": self.file_unique_id,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "original_name": self.original_name,
            "extension": self.extension,
            "source_message_id": self.source_message_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AttachmentSpec":
        return cls(
            kind=value["kind"],
            file_id=value["file_id"],
            file_unique_id=value["file_unique_id"],
            file_size=value.get("file_size"),
            mime_type=value.get("mime_type"),
            original_name=value.get("original_name"),
            extension=value["extension"],
            source_message_id=value["source_message_id"],
        )


@dataclass
class DownloadedAttachment:
    spec: AttachmentSpec
    data: bytes | None
    warning: str | None = None


@dataclass(frozen=True)
class CapturedItem:
    chat_id: int
    message_id: int
    revision: int
    status: str
    category: str | None
    path: Path
    already_known: bool = False


@dataclass(frozen=True)
class ProcessingJob:
    chat_id: int
    message_id: int
    revision: int
    category: str | None
    path: Path


@dataclass(frozen=True)
class GeneratedFile:
    """A generated file waiting to be committed into an item directory."""

    relative_path: Path
    source_path: Path


@dataclass
class ProcessingResult:
    """Changes produced in a revision-scoped processing workspace."""

    message_markdown: str
    source_markdown: str | None = None
    generated_files: list[GeneratedFile] = field(default_factory=list)


@dataclass(frozen=True)
class ProcessingIssue:
    """One stable, searchable problem reported by a processing step."""

    reason: str
    message: str
    target: str | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.reason) is None:
            raise ValueError(f"Invalid processing issue reason: {self.reason}")


@dataclass(frozen=True)
class ProcessingStepOutcome:
    """Declared result of a step which completed with known problems."""

    status: Literal["succeeded", "partial", "failed"]
    issues: tuple[ProcessingIssue, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "succeeded" and self.issues:
            raise ValueError("A succeeded processing outcome cannot contain issues")
        if self.status in ("partial", "failed") and not self.issues:
            raise ValueError(f"A {self.status} processing outcome requires an issue")

    @classmethod
    def partial(cls, *issues: ProcessingIssue) -> "ProcessingStepOutcome":
        return cls("partial", tuple(issues))

    @classmethod
    def failed(cls, *issues: ProcessingIssue) -> "ProcessingStepOutcome":
        return cls("failed", tuple(issues))
