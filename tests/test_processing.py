import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from info_triage.models import (
    GeneratedFile,
    ProcessingIssue,
    ProcessingStepOutcome,
)
from info_triage.processing import (
    ProcessingCoordinator,
    ProcessingPipeline,
    ProcessingWorker,
)
from info_triage.rendering import render_capture_payloads
from info_triage.storage import CaptureStore


def wait_for_status(store, message_id, status, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = store.get_item(10, message_id)
        if item is not None and item["status"] == status:
            return item
        time.sleep(0.01)
    current = store.get_item(10, message_id)
    raise AssertionError(
        f"item {message_id} did not reach {status}; current={dict(current or {})}"
    )


class AppendStep:
    name = "append"

    def __init__(self, calls=None):
        self.calls = calls

    def applies(self, job):
        return True

    def run(self, job, result, workspace):
        if self.calls is not None:
            self.calls.append(job.message_id)
        result.message_markdown += "\nprocessed"


class ProcessingTests(unittest.TestCase):
    def test_no_steps_promotes_directly(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            pipeline = ProcessingPipeline()
            worker = ProcessingWorker(store, pipeline)
            coordinator = ProcessingCoordinator(store, pipeline, worker)

            item = store.capture(
                10, 1, "plain", received_at="2026-08-09T10:00:00+00:00"
            )
            self.assertEqual(store.get_item(10, 1)["status"], "received")
            coordinator.submit(item)

            self.assertEqual(store.get_item(10, 1)["status"], "ready")
            self.assertTrue((Path(temporary) / "inbox" / "2026-08-09_1").is_dir())

    def test_worker_is_fifo_and_commits_processed_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            store = CaptureStore(Path(temporary))
            pipeline = ProcessingPipeline([AppendStep(calls)])
            worker = ProcessingWorker(store, pipeline)
            coordinator = ProcessingCoordinator(store, pipeline, worker)
            first = store.capture(
                10, 1, "first", received_at="2026-08-09T10:00:00+00:00"
            )
            second = store.capture(
                10, 2, "second", received_at="2026-08-09T10:00:01+00:00"
            )
            coordinator.submit(first)
            coordinator.submit(second)
            worker.start()
            try:
                wait_for_status(store, 1, "ready")
                wait_for_status(store, 2, "ready")
            finally:
                worker.stop()

            self.assertEqual(calls, [1, 2])
            message = (
                Path(temporary) / "inbox" / "2026-08-09_1" / "message.md"
            ).read_text()
            self.assertEqual(
                message,
                "---\ncategory: Other\n---\n\nfirst\nprocessed",
            )
            records = [
                json.loads(line)
                for line in (
                    Path(temporary) / "logs" / "processor-runs.jsonl"
                ).read_text().splitlines()
            ]
            self.assertEqual([record["outcome"] for record in records], ["succeeded"] * 2)
            self.assertNotIn("processor_input", records[0])
            self.assertNotIn("issues", records[0])
            self.assertNotIn("result", records[0])

    def test_worker_commits_generated_files_from_workspace(self):
        class GenerateStep(AppendStep):
            name = "generate"

            def run(self, job, result, workspace):
                generated = workspace / "ocr.txt"
                generated.write_text("recognized text")
                result.generated_files.append(
                    GeneratedFile(Path("generated/ocr.txt"), generated)
                )

        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            pipeline = ProcessingPipeline([GenerateStep()])
            worker = ProcessingWorker(store, pipeline)
            coordinator = ProcessingCoordinator(store, pipeline, worker)
            item = store.capture(
                10, 1, "image", received_at="2026-08-09T10:00:00+00:00"
            )
            worker.start()
            coordinator.submit(item)
            try:
                wait_for_status(store, 1, "ready")
            finally:
                worker.stop()

            generated = (
                Path(temporary) / "inbox" / "2026-08-09_1" / "generated" / "ocr.txt"
            )
            self.assertEqual(generated.read_text(), "recognized text")

    def test_failure_keeps_item_and_worker_continues(self):
        class SometimesFails(AppendStep):
            name = "sometimes-fails"

            def run(self, job, result, workspace):
                if job.message_id == 1:
                    raise RuntimeError("deliberate failure")
                super().run(job, result, workspace)

        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            pipeline = ProcessingPipeline([SometimesFails()])
            worker = ProcessingWorker(store, pipeline)
            coordinator = ProcessingCoordinator(store, pipeline, worker)
            for message_id in (1, 2):
                item = store.capture(
                    10,
                    message_id,
                    str(message_id),
                    received_at=f"2026-08-09T10:00:0{message_id}+00:00",
                )
                coordinator.submit(item)
            worker.start()
            try:
                failed = wait_for_status(store, 1, "failed")
                wait_for_status(store, 2, "ready")
            finally:
                worker.stop()

            self.assertEqual(failed["processing_step"], "sometimes-fails")
            self.assertIn("deliberate failure", failed["error"])
            self.assertTrue((Path(temporary) / "staging" / "2026-08-09_1").is_dir())
            failure_record = json.loads(
                (Path(temporary) / "logs" / "processor-runs.jsonl")
                .read_text()
                .splitlines()[0]
            )
            self.assertEqual(failure_record["outcome"], "failed")
            self.assertEqual(
                failure_record["issues"][0]["error_type"], "RuntimeError"
            )
            self.assertIn("RuntimeError: deliberate failure", failure_record["traceback"])

    def test_declared_failure_rolls_back_and_later_step_continues(self):
        class MutateThenFail(AppendStep):
            name = "declared-failure"

            def run(self, job, result, workspace):
                result.message_markdown += "\nshould be discarded"
                generated = workspace / "discarded.txt"
                generated.write_text("discard me")
                result.generated_files.append(
                    GeneratedFile(Path("generated/discarded.txt"), generated)
                )
                return ProcessingStepOutcome.failed(
                    ProcessingIssue("known-problem", "expected failure", "target-1")
                )

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            store = CaptureStore(data_dir)
            pipeline = ProcessingPipeline([MutateThenFail(), AppendStep()])
            worker = ProcessingWorker(store, pipeline)
            coordinator = ProcessingCoordinator(store, pipeline, worker)
            item = store.capture(
                10, 1, "original", received_at="2026-08-09T10:00:00+00:00"
            )
            worker.start()
            coordinator.submit(item)
            try:
                wait_for_status(store, 1, "ready")
            finally:
                worker.stop()

            ready = data_dir / "inbox" / "2026-08-09_1"
            self.assertEqual(
                (ready / "message.md").read_text(),
                "---\ncategory: Other\n---\n\noriginal\nprocessed",
            )
            self.assertFalse((ready / "generated" / "discarded.txt").exists())
            stats = {row["processor"]: row for row in store.processor_statistics()}
            self.assertEqual(stats["declared-failure"]["failed"], 1)
            self.assertEqual(stats["append"]["succeeded"], 1)

    def test_failure_log_preserves_multiline_unicode_input_on_one_json_line(self):
        class DeclaredFailure(AppendStep):
            name = "unicode-failure"

            def run(self, job, result, workspace):
                return ProcessingStepOutcome.failed(
                    ProcessingIssue("bad-target", "could not process", "https://例.test/長")
                )

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            store = CaptureStore(data_dir)
            pipeline = ProcessingPipeline([DeclaredFailure()])
            worker = ProcessingWorker(store, pipeline)
            coordinator = ProcessingCoordinator(store, pipeline, worker)
            source = "first line\nsecond line 世界"
            item = store.capture(
                10, 1, source, received_at="2026-08-09T10:00:00+00:00"
            )
            worker.start()
            coordinator.submit(item)
            try:
                wait_for_status(store, 1, "ready")
            finally:
                worker.stop()

            log_text = (data_dir / "logs" / "processor-runs.jsonl").read_text()
            self.assertEqual(len(log_text.splitlines()), 1)
            record = json.loads(log_text)
            self.assertEqual(record["processor_input"], source)
            self.assertEqual(record["issues"][0]["target"], "https://例.test/長")
            self.assertIn("unicode-failure:bad-target", record["lookup_keys"])

    def test_edit_supersedes_running_revision(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingStep(AppendStep):
            name = "blocking"

            def run(self, job, result, workspace):
                if job.revision == 1:
                    started.set()
                    self.assert_release()
                result.message_markdown += f"\nrevision {job.revision}"

            @staticmethod
            def assert_release():
                if not release.wait(3):
                    raise RuntimeError("test release timed out")

        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            pipeline = ProcessingPipeline([BlockingStep()])
            worker = ProcessingWorker(store, pipeline)
            coordinator = ProcessingCoordinator(store, pipeline, worker)
            first = store.capture(10, 1, "old", received_at="2026-08-09T10:00:00+00:00")
            worker.start()
            coordinator.submit(first)
            self.assertTrue(started.wait(3))

            edited = store.capture(
                10,
                1,
                "new",
                edited_at="2026-08-09T10:05:00+00:00",
                received_at="2026-08-09T10:00:00+00:00",
            )
            coordinator.submit(edited)
            release.set()
            try:
                item = wait_for_status(store, 1, "ready")
            finally:
                worker.stop()

            self.assertEqual(item["revision"], 2)
            message = (
                Path(temporary) / "inbox" / "2026-08-09_1" / "message.md"
            ).read_text()
            self.assertEqual(
                message,
                "---\ncategory: Other\n---\n\nnew\nrevision 2",
            )
            source = (
                Path(temporary) / "inbox" / "2026-08-09_1" / "source.md"
            ).read_text()
            self.assertEqual(source, "new")
            stats = store.processor_statistics()[0]
            self.assertEqual(stats["runs"], 2)
            self.assertEqual(stats["succeeded"], 2)

    def test_category_change_supersedes_running_revision(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingStep(AppendStep):
            name = "blocking-category"

            def run(self, job, result, workspace):
                if job.revision == 1:
                    started.set()
                    if not release.wait(3):
                        raise RuntimeError("test release timed out")
                result.message_markdown += f"\nrevision {job.revision}"

        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            pipeline = ProcessingPipeline([BlockingStep()])
            worker = ProcessingWorker(store, pipeline)
            coordinator = ProcessingCoordinator(store, pipeline, worker)
            first = store.capture(
                10, 1, "source", received_at="2026-08-09T10:00:00+00:00"
            )
            worker.start()
            coordinator.submit(first)
            self.assertTrue(started.wait(3))

            categorized = store.categorize(10, 1, "Life")
            coordinator.submit(categorized)
            release.set()
            try:
                item = wait_for_status(store, 1, "ready")
            finally:
                worker.stop()

            self.assertEqual(item["revision"], 2)
            self.assertEqual(item["category"], "Life")
            message = (
                Path(temporary) / "inbox" / "2026-08-09_1" / "message.md"
            ).read_text()
            self.assertEqual(
                message,
                "---\ncategory: Life\n---\n\nsource\nrevision 2",
            )

    def test_category_change_rebuilds_source_from_raw_telegram_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            store = CaptureStore(data_dir)
            payload = {
                "message_id": 1,
                "date": 100,
                "chat": {"id": 10, "type": "private"},
                "text": "Raw Telegram text",
            }
            source = render_capture_payloads([payload])
            first = store.capture(
                10,
                1,
                source,
                received_at="2026-08-09T10:00:00+00:00",
                telegram_payload=payload,
            )
            store.promote_if_current(first)

            # The retained raw payload, not either derived Markdown file, owns
            # source reconstruction for the next revision.
            ready = data_dir / "inbox" / "2026-08-09_1"
            (ready / "source.md").write_text("stale transformed text")
            (ready / "message.md").write_text("stale transformed text")
            categorized = store.categorize(10, 1, "Life")
            store.promote_if_current(categorized)

            self.assertEqual((ready / "source.md").read_text(), source)
            self.assertEqual(
                (ready / "message.md").read_text(),
                "---\ncategory: Life\n---\n\n" + source,
            )

    def test_restart_returns_interrupted_processing_to_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            store = CaptureStore(data_dir)
            store.capture(10, 1, "plain", received_at="2026-08-09T10:00:00+00:00")
            self.assertIsNotNone(store.claim_next_received())
            self.assertEqual(store.get_item(10, 1)["status"], "processing")

            recovered = CaptureStore(data_dir)
            self.assertEqual(recovered.get_item(10, 1)["status"], "received")

            interrupted = recovered.claim_next_received()
            self.assertIsNotNone(interrupted)
            interrupted.path.rename(data_dir / "inbox" / interrupted.path.name)
            promoted = CaptureStore(data_dir)
            self.assertEqual(promoted.get_item(10, 1)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
