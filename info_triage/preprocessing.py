"""Automatic preprocessing steps for captured Telegram items."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import (
    StepConfig,
    TextCleaningConfig,
    URLResolutionConfig,
    VoiceTranscriptionConfig,
)
from .extractors.instagram.transcription import (
    Transcriber,
    make_transcriber,
    resolve_backend_and_model,
    resolve_transcription_threads,
)
from .models import (
    ProcessingIssue,
    ProcessingJob,
    ProcessingResult,
    ProcessingStepOutcome,
)
from .rendering import render_capture_payloads
from .utilities.text_cleaning import clean_text
from .utilities.url_resolution import URLResolver, replace_urls

NO_SPEECH_TEXT = "[No speech recognized]"
SEGMENT_HEADING_RE = re.compile(r"^## Segment [1-9][0-9]* — [a-z-]+(?: [a-z-]+)*$")


class VoiceTranscriptionStep:
    """Materialize downloaded Telegram voice attachments into their segments."""

    name = "voice-transcription"

    def __init__(
        self,
        model_cache_dir: Path,
        *,
        backend: str | None = None,
        model_name: str | None = None,
        threads: int | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        self.model_cache_dir = model_cache_dir
        self.backend = backend
        self.model_name = model_name
        self.threads = threads
        self._transcriber = transcriber

    @staticmethod
    def _metadata(job: ProcessingJob) -> dict[str, Any]:
        return json.loads((job.path / "metadata.json").read_text(encoding="utf-8"))

    @classmethod
    def _voice_attachments(cls, job: ProcessingJob) -> list[dict[str, Any]]:
        attachments = cls._metadata(job).get("attachments", [])
        if not isinstance(attachments, list):
            raise ValueError("metadata attachments must be a list")
        return [
            attachment
            for attachment in attachments
            if isinstance(attachment, dict) and attachment.get("kind") == "voice"
        ]

    def applies(self, job: ProcessingJob) -> bool:
        try:
            return bool(self._voice_attachments(job))
        except (OSError, ValueError, json.JSONDecodeError):
            return True

    def _get_transcriber(self) -> Transcriber:
        if self._transcriber is None:
            backend, model_name = resolve_backend_and_model(
                self.backend, self.model_name
            )
            self._transcriber = make_transcriber(
                backend,
                model_name,
                self.model_cache_dir,
                resolve_transcription_threads(self.threads),
            )
        return self._transcriber

    @staticmethod
    def _attachment_path(job: ProcessingJob, attachment: dict[str, Any]) -> Path:
        relative_path = attachment.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            warning = attachment.get("warning") or "the attachment was not downloaded"
            raise RuntimeError(f"Voice attachment is unavailable: {warning}")

        item_root = job.path.resolve()
        attachment_path = (job.path / relative_path).resolve()
        if not attachment_path.is_relative_to(item_root):
            raise ValueError(f"Unsafe voice attachment path: {relative_path}")
        if not attachment_path.is_file():
            raise FileNotFoundError(f"Voice attachment is missing: {relative_path}")
        return attachment_path

    @staticmethod
    def _payloads(job: ProcessingJob) -> list[dict[str, Any]]:
        value = json.loads((job.path / "telegram.json").read_text(encoding="utf-8"))
        payloads = value.get("messages") if isinstance(value, dict) else None
        if payloads is None and isinstance(value, dict):
            payloads = [value]
        if not isinstance(payloads, list) or not all(
            isinstance(payload, dict) for payload in payloads
        ):
            raise ValueError("telegram.json does not contain Telegram message payloads")
        return payloads

    def run(
        self,
        job: ProcessingJob,
        result: ProcessingResult,
        workspace: Path,
    ) -> ProcessingStepOutcome | None:
        del workspace
        voice_files: list[tuple[int, Path]] = []
        try:
            attachments = self._voice_attachments(job)
            for attachment in attachments:
                source_message_id = attachment.get("source_message_id")
                if not isinstance(source_message_id, int):
                    return ProcessingStepOutcome.failed(
                        ProcessingIssue(
                            "invalid-input",
                            "Voice attachment has no valid source_message_id",
                        )
                    )
                try:
                    attachment_path = self._attachment_path(job, attachment)
                except FileNotFoundError as error:
                    return ProcessingStepOutcome.failed(
                        ProcessingIssue(
                            "attachment-missing",
                            str(error),
                            target=str(attachment.get("path") or ""),
                            error_type=type(error).__name__,
                        )
                    )
                except RuntimeError as error:
                    return ProcessingStepOutcome.failed(
                        ProcessingIssue(
                            "attachment-unavailable",
                            str(error),
                            target=str(attachment.get("path") or ""),
                            error_type=type(error).__name__,
                        )
                    )
                voice_files.append((source_message_id, attachment_path))
            payloads = self._payloads(job)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            return ProcessingStepOutcome.failed(
                ProcessingIssue(
                    "invalid-input",
                    str(error),
                    error_type=type(error).__name__,
                )
            )

        payload_message_ids = {
            payload.get("message_id")
            for payload in payloads
            if isinstance(payload.get("message_id"), int)
        }
        absent = {source_message_id for source_message_id, _ in voice_files} - payload_message_ids
        if absent:
            return ProcessingStepOutcome.failed(
                ProcessingIssue(
                    "invalid-input",
                    "Voice attachment sources are missing from telegram.json: "
                    + ", ".join(str(value) for value in sorted(absent)),
                    target=", ".join(str(value) for value in sorted(absent)),
                )
            )

        transcripts: dict[int, list[str]] = {}
        transcriber = self._get_transcriber()
        for source_message_id, attachment_path in voice_files:
            transcript = transcriber.transcribe(
                attachment_path,
                language=None,
                vad=True,
                keep_segments=False,
            )
            if transcript.status == "complete" and transcript.text.strip():
                text = transcript.text.strip()
            elif transcript.status == "no_speech":
                text = NO_SPEECH_TEXT
            else:
                detail = transcript.error or transcript.status
                return ProcessingStepOutcome.failed(
                    ProcessingIssue(
                        "transcription-failed",
                        f"Could not transcribe voice attachment {attachment_path.name}: {detail}",
                        target=str(attachment_path),
                        error_type="TranscriptionError",
                    )
                )

            transcripts.setdefault(source_message_id, []).append(text)

        content = render_capture_payloads(payloads, voice_transcripts=transcripts)
        result.source_markdown = content
        result.message_markdown = content


class URLResolutionStep:
    """Resolve recognized short URLs without failing an otherwise useful item."""

    name = "url-resolution"

    def __init__(
        self,
        *,
        timeout_seconds: float,
        retries: int,
        max_html_bytes: int,
        resolve_all: bool,
        resolver: URLResolver | None = None,
    ) -> None:
        self.resolver = resolver or URLResolver(
            timeout=timeout_seconds,
            retries=retries,
            max_html_bytes=max_html_bytes,
            resolve_all=resolve_all,
        )

    @staticmethod
    def applies(job: ProcessingJob) -> bool:
        del job
        return True

    def run(
        self,
        job: ProcessingJob,
        result: ProcessingResult,
        workspace: Path,
    ) -> ProcessingStepOutcome | None:
        del job, workspace
        failure_count = len(getattr(self.resolver, "failures", ()))
        result.message_markdown = replace_urls(result.message_markdown, self.resolver)
        failures = tuple(getattr(self.resolver, "failures", ()))[failure_count:]
        if not failures:
            return None
        return ProcessingStepOutcome.partial(
            *(
                ProcessingIssue(
                    self._failure_reason(message),
                    message,
                    target=url,
                )
                for url, message in failures
            )
        )

    @staticmethod
    def _failure_reason(message: str) -> str:
        if message == "redirect loop detected":
            return "redirect-loop"
        if message.startswith("shortener chain did not expose"):
            return "destination-not-found"
        if message.startswith("exceeded "):
            return "redirect-limit"
        if message:
            return "request-error"
        return "unknown-resolution-error"


class TextCleaningStep:
    """Apply the existing cautious text cleaner to the materialized item."""

    name = "text-cleaning"

    @staticmethod
    def applies(job: ProcessingJob) -> bool:
        del job
        return True

    def run(
        self,
        job: ProcessingJob,
        result: ProcessingResult,
        workspace: Path,
    ) -> None:
        del job, workspace
        headings = []
        protected_lines = []
        for line in result.message_markdown.splitlines(keepends=True):
            content = line.removesuffix("\n")
            if SEGMENT_HEADING_RE.fullmatch(content):
                placeholder = f"#+INFO_TRIAGE_SEGMENT_{len(headings)}"
                headings.append(content)
                protected_lines.append(placeholder + ("\n" if line.endswith("\n") else ""))
            else:
                protected_lines.append(line)
        cleaned = clean_text(
            "".join(protected_lines),
            resolve_links=False,
        )
        for index, heading in enumerate(headings):
            cleaned = cleaned.replace(f"#+INFO_TRIAGE_SEGMENT_{index}", heading)
        result.message_markdown = cleaned


def processing_steps_from_config(configs: tuple[StepConfig, ...]) -> list[Any]:
    """Construct ordered processing steps from validated configuration."""
    steps = []
    for config in configs:
        if isinstance(config, VoiceTranscriptionConfig):
            steps.append(
                VoiceTranscriptionStep(
                    config.model_cache_dir,
                    backend=config.backend,
                    model_name=config.model,
                    threads=config.threads,
                )
            )
        elif isinstance(config, URLResolutionConfig):
            steps.append(
                URLResolutionStep(
                    timeout_seconds=config.timeout_seconds,
                    retries=config.retries,
                    max_html_bytes=config.max_html_bytes,
                    resolve_all=config.resolve_all,
                )
            )
        elif isinstance(config, TextCleaningConfig):
            steps.append(TextCleaningStep())
        else:
            raise TypeError(f"Unsupported processing configuration: {config!r}")
    return steps
