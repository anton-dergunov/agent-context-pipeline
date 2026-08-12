"""Automatic preprocessing steps for captured Telegram items."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .extractors.instagram.transcription import (
    Transcriber,
    make_transcriber,
    resolve_backend_and_model,
    resolve_transcription_threads,
)
from .models import ProcessingJob, ProcessingResult
from .storage import render_message
from .telegram_bot import render_capture_payloads

NO_SPEECH_TEXT = "[No speech recognized]"


class VoiceTranscriptionStep:
    """Transcribe downloaded Telegram voice attachments into ``message.md``."""

    name = "voice-transcription"

    def __init__(
        self,
        model_cache_dir: Path,
        *,
        transcriber: Transcriber | None = None,
    ) -> None:
        self.model_cache_dir = model_cache_dir
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
        return bool(self._voice_attachments(job))

    def _get_transcriber(self) -> Transcriber:
        if self._transcriber is None:
            backend, model_name = resolve_backend_and_model()
            self._transcriber = make_transcriber(
                backend,
                model_name,
                self.model_cache_dir,
                resolve_transcription_threads(),
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
    ) -> None:
        del workspace
        voice_files: list[tuple[int, Path]] = []
        for attachment in self._voice_attachments(job):
            source_message_id = attachment.get("source_message_id")
            if not isinstance(source_message_id, int):
                raise ValueError("Voice attachment has no valid source_message_id")
            voice_files.append((source_message_id, self._attachment_path(job, attachment)))

        payloads = self._payloads(job)
        payload_message_ids = {
            payload.get("message_id")
            for payload in payloads
            if isinstance(payload.get("message_id"), int)
        }
        absent = {source_message_id for source_message_id, _ in voice_files} - payload_message_ids
        if absent:
            raise ValueError(
                "Voice attachment sources are missing from telegram.json: "
                + ", ".join(str(value) for value in sorted(absent))
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
                raise RuntimeError(
                    f"Could not transcribe voice attachment {attachment_path.name}: {detail}"
                )

            transcripts.setdefault(source_message_id, []).append(text)

        content = render_capture_payloads(payloads, voice_transcripts=transcripts)
        result.message_markdown = render_message(job.category, content)
