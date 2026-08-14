"""A minimal, durable, single-worker processing pipeline."""

import json
import logging
import tempfile
import threading
import time
import traceback
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Protocol

from .models import (
    CapturedItem,
    ProcessingIssue,
    ProcessingJob,
    ProcessingResult,
    ProcessingStepOutcome,
)
from .storage import CAPTURE_DIR, CaptureStore, now_iso

logger = logging.getLogger("info_triage")


class ProcessingStep(Protocol):
    """One future processing operation, executed inside a temporary workspace."""

    name: str

    def applies(self, job: ProcessingJob) -> bool: ...

    def run(
        self, job: ProcessingJob, result: ProcessingResult, workspace: Path
    ) -> ProcessingStepOutcome | None: ...


# The runtime entry point injects its registered production steps. An empty default
# keeps the pipeline reusable for callers that deliberately want pass-through capture.
PROCESSING_STEPS: tuple[ProcessingStep, ...] = ()


class ProcessingPipeline:
    def __init__(self, steps: Iterable[ProcessingStep] | None = None):
        self.steps = tuple(PROCESSING_STEPS if steps is None else steps)

    def steps_for(self, job: ProcessingJob) -> tuple[ProcessingStep, ...]:
        return tuple(step for step in self.steps if step.applies(job))


def _exception_reason(error: Exception) -> str:
    name = type(error).__name__
    parts = []
    start = 0
    for index, character in enumerate(name):
        if index and character.isupper() and not name[index - 1].isupper():
            parts.append(name[start:index].lower())
            start = index
    parts.append(name[start:].lower())
    return "exception-" + "-".join(parts)


class ProcessorTelemetry:
    """Append processor detail logs and update cumulative SQLite counters."""

    def __init__(self, store: CaptureStore) -> None:
        self.store = store
        self.log_path = store.data_dir / "logs" / "processor-runs.jsonl"
        self._lock = threading.Lock()

    def record(
        self,
        job: ProcessingJob,
        processor: str,
        outcome: str,
        duration_ms: float,
        issues: tuple[ProcessingIssue, ...] = (),
        *,
        processor_input: str | None = None,
        traceback_text: str | None = None,
    ) -> None:
        record = {
            "timestamp": now_iso(),
            "duration_ms": round(duration_ms, 3),
            "processor": processor,
            "outcome": outcome,
            "item_id": job.path.name,
            "chat_id": job.chat_id,
            "message_id": job.message_id,
            "revision": job.revision,
        }
        if outcome != "succeeded":
            record["lookup_keys"] = sorted({f"{processor}:{issue.reason}" for issue in issues})
            record["processor_input"] = processor_input
            record["issues"] = [
                {
                    key: value
                    for key, value in (
                        ("reason", issue.reason),
                        ("message", issue.message),
                        ("target", issue.target),
                        ("error_type", issue.error_type),
                    )
                    if value is not None
                }
                for issue in issues
            ]
            if traceback_text is not None:
                record["traceback"] = traceback_text

        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as output:
                output.write(line)
            self.store.record_processor_run(processor, outcome, issues)


class ProcessingWorker:
    """Process the durable SQLite queue using exactly one background thread."""

    def __init__(self, store: CaptureStore, pipeline: ProcessingPipeline):
        self.store = store
        self.pipeline = pipeline
        self.telemetry = ProcessorTelemetry(store)
        self.store.register_processors(tuple(step.name for step in pipeline.steps))
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="info-triage-processing",
            daemon=True,
        )
        self._thread.start()
        self.wake()

    def wake(self) -> None:
        self._wake_event.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                logger.warning(
                    "Processing worker did not stop within %.1f seconds", timeout
                )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            job = self.store.claim_next_received()
            if job is None:
                self._wake_event.clear()
                if self._stop_event.is_set():
                    return
                if self.store.get_item_with_status("received") is not None:
                    continue
                self._wake_event.wait()
                continue
            self._process(job)

    def _process(self, job: ProcessingJob) -> None:
        current_step = None
        try:
            steps = self.pipeline.steps_for(job)
            source = (job.path / CAPTURE_DIR / "source.md").read_text(encoding="utf-8")
            result = ProcessingResult(
                message_markdown=source,
                source_markdown=source,
            )
            prefix = f"{job.path.name}-r{job.revision}-"
            with tempfile.TemporaryDirectory(prefix=prefix) as workspace_text:
                workspace = Path(workspace_text)
                for index, step in enumerate(steps):
                    current_step = step.name
                    if not self.store.set_processing_step(job, current_step):
                        return
                    processor_input = result.message_markdown
                    snapshot = ProcessingResult(
                        result.message_markdown,
                        result.source_markdown,
                        list(result.generated_files),
                        deepcopy(result.links),
                    )
                    step_workspace = workspace / f"step-{index:02d}"
                    step_workspace.mkdir()
                    started = time.monotonic()
                    try:
                        declared_outcome = step.run(job, result, step_workspace)
                        if declared_outcome is None:
                            outcome = ProcessingStepOutcome("succeeded")
                        elif isinstance(declared_outcome, ProcessingStepOutcome):
                            outcome = declared_outcome
                        else:
                            raise TypeError(
                                f"Processor {step.name} returned an invalid outcome: "
                                f"{declared_outcome!r}"
                            )
                    except Exception as error:
                        duration_ms = (time.monotonic() - started) * 1000
                        issue = ProcessingIssue(
                            _exception_reason(error),
                            str(error),
                            error_type=type(error).__name__,
                        )
                        self.telemetry.record(
                            job,
                            step.name,
                            "failed",
                            duration_ms,
                            (issue,),
                            processor_input=processor_input,
                            traceback_text=traceback.format_exc(),
                        )
                        raise

                    if outcome.status == "failed":
                        result.message_markdown = snapshot.message_markdown
                        result.source_markdown = snapshot.source_markdown
                        result.generated_files = snapshot.generated_files
                        result.links = snapshot.links
                    duration_ms = (time.monotonic() - started) * 1000
                    self.telemetry.record(
                        job,
                        step.name,
                        outcome.status,
                        duration_ms,
                        outcome.issues,
                        processor_input=processor_input if outcome.status != "succeeded" else None,
                    )
                current_step = None
                workspace_root = workspace.resolve()
                for generated in result.generated_files:
                    if not generated.source_path.resolve().is_relative_to(
                        workspace_root
                    ):
                        raise ValueError(
                            "Generated files must come from the processing workspace"
                        )
                self.store.promote_if_current(job, result)
        except Exception as error:
            if self.store.fail_if_current(job, str(error), current_step):
                logger.exception(
                    "Processing failed for Telegram item %s", job.message_id
                )


class ProcessingCoordinator:
    """Route captured revisions either directly to inbox or to the worker."""

    def __init__(
        self,
        store: CaptureStore,
        pipeline: ProcessingPipeline,
        worker: ProcessingWorker,
    ):
        self.store = store
        self.pipeline = pipeline
        self.worker = worker

    def submit(self, item: CapturedItem) -> None:
        if item.status != "received":
            return
        job = ProcessingJob(
            item.chat_id,
            item.message_id,
            item.revision,
            item.category,
            item.path,
        )
        if self.pipeline.steps_for(job):
            self.worker.wake()
            return
        self.store.promote_if_current(item)
