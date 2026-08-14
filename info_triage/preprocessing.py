"""Automatic preprocessing steps for captured Telegram items."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import (
    IndexRenderConfig,
    LinkDiscoveryConfig,
    StepConfig,
    TextCleaningConfig,
    URLResolutionConfig,
    VoiceTranscriptionConfig,
)
from .extractors.media.transcription import (
    Transcriber,
    make_transcriber,
    resolve_backend_and_model,
    resolve_transcription_threads,
)
from .index import render_index
from .links import build_link_table, canonicalize_url, link_priority, route_handler
from .models import (
    ProcessingIssue,
    ProcessingJob,
    ProcessingResult,
    ProcessingStepOutcome,
)
from .rendering import SEGMENT_HEADING_RE, render_capture_payloads
from .storage import CAPTURE_DIR
from .utilities.text_cleaning import clean_text
from .utilities.url_resolution import LinkResolution, URLResolver, enrich_links

NO_SPEECH_TEXT = "[No speech recognized]"
LINK_TABLE_NAME = "links.json"
INDEX_NAME = "index.md"


def read_capture_payloads(job: ProcessingJob) -> list[dict[str, Any]]:
    """Return the retained Telegram payloads of an item, in stored order."""
    payload_path = job.path / CAPTURE_DIR / "telegram.json"
    value = json.loads(payload_path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        payloads = value.get("messages", [value])
    else:
        payloads = value
    if not isinstance(payloads, list) or not all(
        isinstance(payload, dict) for payload in payloads
    ):
        raise ValueError("telegram.json does not contain Telegram message payloads")
    return payloads


def write_link_table(
    result: ProcessingResult,
    workspace: Path,
) -> None:
    """Hand the current link table over for commit at the item root."""
    destination = workspace / LINK_TABLE_NAME
    payload = {"links": [entry.to_dict() for entry in result.links]}
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result.put_generated_file(Path(LINK_TABLE_NAME), destination)


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

        attachments_root = (job.path / CAPTURE_DIR / "attachments").resolve()
        attachment_path = (job.path / relative_path).resolve()
        if not attachment_path.is_relative_to(attachments_root):
            raise ValueError(f"Unsafe voice attachment path: {relative_path}")
        if not attachment_path.is_file():
            raise FileNotFoundError(f"Voice attachment is missing: {relative_path}")
        return attachment_path

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
            payloads = read_capture_payloads(job)
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


class LinkDiscoveryStep:
    """Collect every link the item carries into one ordered, canonical table."""

    name = "link-discovery"

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
        issue = None
        try:
            payloads = read_capture_payloads(job)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            # The body still carries visible links, so keep the item moving and
            # report only the entity hrefs that could not be read.
            payloads = []
            issue = ProcessingIssue(
                "invalid-input",
                str(error),
                error_type=type(error).__name__,
            )

        result.links = build_link_table(payloads, result.message_markdown)
        write_link_table(result, workspace)
        return ProcessingStepOutcome.partial(issue) if issue else None


class _BudgetedResolver:
    """Delegate to a resolver until the item's title budget is spent."""

    def __init__(self, resolver: URLResolver, remaining: int) -> None:
        self.resolver = resolver
        self.remaining = remaining

    def resolve_link(self, url: str) -> LinkResolution:
        known = getattr(self.resolver, "link_results", {})
        if url in known:
            return known[url]
        if self.remaining <= 0:
            return LinkResolution(url, None)
        self.remaining -= 1
        return self.resolver.resolve_link(url)


class URLResolutionStep:
    """Resolve URL destinations and titles without blocking a useful item."""

    name = "url-resolution"

    def __init__(
        self,
        *,
        timeout_seconds: float,
        retries: int,
        max_html_bytes: int,
        max_pdf_bytes: int,
        resolve_all: bool,
        resolve_budget: int,
        resolver: URLResolver | None = None,
    ) -> None:
        self.resolve_budget = resolve_budget
        self.resolver = resolver or URLResolver(
            timeout=timeout_seconds,
            retries=retries,
            max_html_bytes=max_html_bytes,
            max_pdf_bytes=max_pdf_bytes,
            resolve_all=resolve_all,
        )

    @staticmethod
    def applies(job: ProcessingJob) -> bool:
        del job
        return True

    def _resolve_table(self, result: ProcessingResult) -> int:
        """Resolve table entries in priority order and return the budget left."""
        remaining = self.resolve_budget
        sole_link = len(result.links) == 1
        for entry in sorted(result.links, key=lambda entry: (entry.priority, entry.n)):
            if remaining <= 0:
                entry.status = "skipped"
                entry.reason = "resolve-budget-exhausted"
                continue
            remaining -= 1
            resolution = self.resolver.resolve_link(entry.raw)
            canonical = canonicalize_url(resolution.url)
            if canonical != entry.canonical:
                # A shortener can hide a paper behind an ordinary-looking URL, so
                # the rank discovery guessed has to be recomputed with it.
                entry.canonical = canonical
                entry.handler = route_handler(canonical)
                entry.priority = link_priority(entry.handler, canonical, sole_link=sole_link)
            entry.title = resolution.title
            if resolution.title:
                entry.status = "resolved"
            else:
                entry.status = "unresolved"
                entry.reason = resolution.reason or "title-not-found"

        seen: dict[str, int] = {}
        for entry in result.links:
            first = seen.get(entry.canonical)
            if first is None:
                seen[entry.canonical] = entry.n
            elif entry.status != "duplicate":
                entry.status = "duplicate"
                entry.duplicate_of = first
        return remaining

    def run(
        self,
        job: ProcessingJob,
        result: ProcessingResult,
        workspace: Path,
    ) -> ProcessingStepOutcome | None:
        del job
        failure_count = len(getattr(self.resolver, "failures", ()))
        remaining = self._resolve_table(result)
        result.message_markdown = enrich_links(
            result.message_markdown,
            _BudgetedResolver(self.resolver, remaining),
        )
        if result.links:
            write_link_table(result, workspace)
        failures = tuple(getattr(self.resolver, "failures", ()))[failure_count:]
        reasons = tuple(getattr(self.resolver, "failure_reasons", ()))[failure_count:]
        if not failures:
            return None
        return ProcessingStepOutcome.partial(
            *(
                ProcessingIssue(
                    reasons[index] if index < len(reasons) else self._failure_reason(message),
                    message,
                    target=url,
                )
                for index, (url, message) in enumerate(failures)
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
        if message == "no trustworthy title found":
            return "title-not-found"
        if message.startswith("unsupported content type") or message.startswith(
            "could not parse PDF metadata"
        ):
            return "unsupported-content"
        if message.startswith("response exceeded"):
            return "response-too-large"
        if message.startswith("unsafe URL:"):
            return "unsafe-url"
        if message.startswith("HTTP "):
            return "http-error"
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


class IndexRenderStep:
    """Write the item's `index.md`, the only per-item contract the laptop reads."""

    name = "index-render"

    def __init__(self, *, linklist_threshold: int) -> None:
        self.linklist_threshold = linklist_threshold

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
        # This step never declares failure: a rolled-back index leaves a ready
        # item without the one file synchronization requires, which would hold up
        # every other item. Whatever cannot be read is simply left out.
        issues = []
        try:
            metadata = json.loads((job.path / "metadata.json").read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("metadata.json does not contain an object")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            metadata = {}
            issues.append(
                ProcessingIssue("invalid-input", str(error), error_type=type(error).__name__)
            )
        try:
            payloads = read_capture_payloads(job)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            payloads = []
            issues.append(
                ProcessingIssue("invalid-input", str(error), error_type=type(error).__name__)
            )

        destination = workspace / INDEX_NAME
        destination.write_text(
            render_index(
                job.path.name,
                metadata,
                payloads,
                result.message_markdown,
                result.links,
                linklist_threshold=self.linklist_threshold,
            ),
            encoding="utf-8",
        )
        result.put_generated_file(Path(INDEX_NAME), destination)
        return ProcessingStepOutcome.partial(*issues) if issues else None


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
                    max_pdf_bytes=config.max_pdf_bytes,
                    resolve_all=config.resolve_all,
                    resolve_budget=config.resolve_budget,
                )
            )
        elif isinstance(config, TextCleaningConfig):
            steps.append(TextCleaningStep())
        elif isinstance(config, LinkDiscoveryConfig):
            steps.append(LinkDiscoveryStep())
        elif isinstance(config, IndexRenderConfig):
            steps.append(IndexRenderStep(linklist_threshold=config.linklist_threshold))
        else:
            raise TypeError(f"Unsupported processing configuration: {config!r}")
    return steps
