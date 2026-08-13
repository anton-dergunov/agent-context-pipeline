"""A minimal, durable, single-worker processing pipeline."""

import logging
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from .models import CapturedItem, ProcessingJob, ProcessingResult
from .storage import CaptureStore, original_content, render_message

logger = logging.getLogger("info_triage")


class ProcessingStep(Protocol):
    """One future processing operation, executed inside a temporary workspace."""

    name: str

    def applies(self, job: ProcessingJob) -> bool: ...

    def run(
        self, job: ProcessingJob, result: ProcessingResult, workspace: Path
    ) -> None: ...


# The runtime entry point injects its registered production steps. An empty default
# keeps the pipeline reusable for callers that deliberately want pass-through capture.
PROCESSING_STEPS: tuple[ProcessingStep, ...] = ()


class ProcessingPipeline:
    def __init__(self, steps: Iterable[ProcessingStep] | None = None):
        self.steps = tuple(PROCESSING_STEPS if steps is None else steps)

    def steps_for(self, job: ProcessingJob) -> tuple[ProcessingStep, ...]:
        return tuple(step for step in self.steps if step.applies(job))


class ProcessingWorker:
    """Process the durable SQLite queue using exactly one background thread."""

    def __init__(self, store: CaptureStore, pipeline: ProcessingPipeline):
        self.store = store
        self.pipeline = pipeline
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
            source_path = job.path / "source.md"
            if source_path.is_file():
                source = source_path.read_text(encoding="utf-8")
            else:
                source = original_content(
                    job.category,
                    (job.path / "message.md").read_text(encoding="utf-8"),
                )
            result = ProcessingResult(
                message_markdown=source,
                source_markdown=source,
            )
            prefix = f"{job.path.name}-r{job.revision}-"
            with tempfile.TemporaryDirectory(prefix=prefix) as workspace_text:
                workspace = Path(workspace_text)
                for step in steps:
                    current_step = step.name
                    if not self.store.set_processing_step(job, current_step):
                        return
                    step.run(job, result, workspace)
                result.message_markdown = render_message(
                    job.category, result.message_markdown
                )
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
