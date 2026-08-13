import time

import pytest

from info_triage.extractors.instagram.transcription import TranscriptResult
from info_triage.models import (
    AttachmentSpec,
    DownloadedAttachment,
    ProcessingJob,
    ProcessingResult,
)
from info_triage.preprocessing import (
    TextCleaningStep,
    URLResolutionStep,
    VoiceTranscriptionStep,
)
from info_triage.processing import ProcessingCoordinator, ProcessingPipeline, ProcessingWorker
from info_triage.storage import CaptureStore


def telegram_payload(message_id, date, **values):
    return {
        "message_id": message_id,
        "date": date,
        "chat": {"id": 10, "type": "private"},
        "from": {"id": 20, "first_name": "Owner", "is_bot": False},
        **values,
    }


def attachment(kind, source_message_id, *, data=b"media"):
    extensions = {
        "photo": ".jpg",
        "document": ".txt",
        "video": ".mp4",
        "animation": ".mp4",
        "audio": ".mp3",
        "voice": ".ogg",
        "video_note": ".mp4",
    }
    spec = AttachmentSpec(
        kind,
        f"{kind}-{source_message_id}",
        f"unique-{kind}-{source_message_id}",
        len(data) if data is not None else None,
        None,
        None,
        extensions[kind],
        source_message_id,
    )
    return DownloadedAttachment(spec, data, None if data is not None else "download failed")


class FakeTranscriber:
    backend = "fake"
    model_name = "fake"
    load_seconds = 0.0

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return self.results.pop(0)


def result(status="complete", text="recognized text", error=None):
    return TranscriptResult(
        source_file="voice.ogg",
        status=status,
        text=text,
        error=error,
    )


def staged_job(tmp_path, payloads, attachments, content=""):
    store = CaptureStore(tmp_path)
    primary = min(payloads, key=lambda value: (value["date"], value["message_id"]))
    item = store.capture(
        10,
        primary["message_id"],
        content,
        received_at="2026-08-09T10:00:00+00:00",
        telegram_payload=payloads[0] if len(payloads) == 1 else {"messages": payloads},
        attachments=attachments,
        source_message_ids=[payload["message_id"] for payload in payloads],
    )
    job = ProcessingJob(
        item.chat_id,
        item.message_id,
        item.revision,
        item.category,
        item.path,
    )
    source = (job.path / "message.md").read_text(encoding="utf-8")
    return store, item, job, ProcessingResult(source)


@pytest.mark.parametrize(
    "kind",
    ["photo", "document", "video", "animation", "audio", "video_note"],
)
def test_voice_step_does_not_apply_to_other_attachment_types(tmp_path, kind):
    payload = telegram_payload(1, 100, text="keep")
    _, _, job, _ = staged_job(tmp_path, [payload], [attachment(kind, 1)], "keep")
    step = VoiceTranscriptionStep(tmp_path / "models", transcriber=FakeTranscriber())

    assert not step.applies(job)


def test_voice_step_applies_to_voice_attachment(tmp_path):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    _, _, job, _ = staged_job(tmp_path, [payload], [attachment("voice", 1)])
    step = VoiceTranscriptionStep(tmp_path / "models", transcriber=FakeTranscriber())

    assert step.applies(job)


def test_voice_only_item_renders_transcript_and_expected_options(tmp_path):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    _, _, job, processing_result = staged_job(tmp_path, [payload], [attachment("voice", 1)])
    transcriber = FakeTranscriber(result(text="Hola 世界"))
    step = VoiceTranscriptionStep(tmp_path / "models", transcriber=transcriber)

    step.run(job, processing_result, tmp_path / "workspace")

    assert processing_result.message_markdown == "## Segment 1 — voice\n\nHola 世界"
    assert processing_result.source_markdown == processing_result.message_markdown
    _, options = transcriber.calls[0]
    assert options == {"language": None, "vad": True, "keep_segments": False}


def test_voice_caption_and_grouped_messages_keep_source_order(tmp_path):
    payloads = [
        telegram_payload(1, 100, text="Before"),
        telegram_payload(
            2,
            101,
            caption="Voice caption",
            voice={
                "file_id": "voice-2",
                "file_unique_id": "unique-voice-2",
                "duration": 1,
            },
        ),
        telegram_payload(3, 102, text="After"),
    ]
    _, _, job, processing_result = staged_job(
        tmp_path,
        payloads,
        [attachment("voice", 2)],
        "Before\n\nVoice caption\n\nAfter",
    )
    step = VoiceTranscriptionStep(
        tmp_path / "models", transcriber=FakeTranscriber(result(text="Middle"))
    )

    step.run(job, processing_result, tmp_path / "workspace")

    assert processing_result.message_markdown == (
        "## Segment 1 — text\n\nBefore\n\n"
        "## Segment 2 — voice\n\nMiddle\n\nVoice caption\n\n"
        "## Segment 3 — text\n\nAfter"
    )


def test_multiple_voice_notes_and_forwarded_note_render_correctly(tmp_path):
    forwarded = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
        forward_origin={
            "type": "channel",
            "date": 50,
            "chat": {"id": -1001, "type": "channel", "title": "Source"},
            "message_id": 9,
        },
    )
    second_voice = telegram_payload(
        2,
        101,
        voice={"file_id": "voice-2", "file_unique_id": "unique-voice-2", "duration": 1},
    )
    note = telegram_payload(3, 102, text="My note")
    _, _, job, processing_result = staged_job(
        tmp_path,
        [forwarded, second_voice, note],
        [attachment("voice", 1), attachment("voice", 2)],
    )
    step = VoiceTranscriptionStep(
        tmp_path / "models",
        transcriber=FakeTranscriber(result(text="Forwarded"), result(text="Second")),
    )

    step.run(job, processing_result, tmp_path / "workspace")

    assert processing_result.message_markdown == (
        "## Segment 1 — forwarded voice\n\nForwarded\n\n"
        "## Segment 2 — voice\n\nSecond\n\n"
        "## Segment 3 — text\n\nMy note"
    )


def test_no_speech_is_delivered_with_explicit_label(tmp_path):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    _, _, job, processing_result = staged_job(tmp_path, [payload], [attachment("voice", 1)])
    step = VoiceTranscriptionStep(
        tmp_path / "models", transcriber=FakeTranscriber(result(status="no_speech", text=""))
    )

    step.run(job, processing_result, tmp_path / "workspace")

    assert processing_result.message_markdown.endswith("[No speech recognized]")


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        (result(status="failed", text="", error="decoder failed"), "decoder failed"),
        (result(status="no_audio", text=""), "no_audio"),
    ],
)
def test_transcription_failure_stops_processing(tmp_path, transcript, expected):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    _, _, job, processing_result = staged_job(tmp_path, [payload], [attachment("voice", 1)])
    step = VoiceTranscriptionStep(tmp_path / "models", transcriber=FakeTranscriber(transcript))

    with pytest.raises(RuntimeError, match=expected):
        step.run(job, processing_result, tmp_path / "workspace")


def test_missing_download_stops_processing(tmp_path):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    _, _, job, processing_result = staged_job(
        tmp_path, [payload], [attachment("voice", 1, data=None)]
    )
    step = VoiceTranscriptionStep(tmp_path / "models", transcriber=FakeTranscriber())

    with pytest.raises(RuntimeError, match="download failed"):
        step.run(job, processing_result, tmp_path / "workspace")


def wait_for_status(store, message_id, status):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        item = store.get_item(10, message_id)
        if item["status"] == status:
            return item
        time.sleep(0.01)
    raise AssertionError(f"item {message_id} did not become {status}")


def test_ready_item_is_not_backfilled_but_later_revision_is_processed(tmp_path):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    store, item, _, _ = staged_job(tmp_path, [payload], [attachment("voice", 1)])
    assert store.promote_if_current(item)
    transcriber = FakeTranscriber(result(text="After revision"))
    step = VoiceTranscriptionStep(tmp_path / "models", transcriber=transcriber)
    pipeline = ProcessingPipeline([step])
    worker = ProcessingWorker(store, pipeline)
    coordinator = ProcessingCoordinator(store, pipeline, worker)

    worker.start()
    try:
        time.sleep(0.05)
        assert transcriber.calls == []
        revised = store.categorize(10, 1, "Life")
        coordinator.submit(revised)
        wait_for_status(store, 1, "ready")
    finally:
        worker.stop()

    message = (tmp_path / "inbox" / "2026-08-09_1" / "message.md").read_text()
    assert message == (
        "---\ncategory: Life\n---\n\n"
        "## Segment 1 — voice\n\nAfter revision"
    )
    source = (tmp_path / "inbox" / "2026-08-09_1" / "source.md").read_text()
    assert source == "## Segment 1 — voice\n\nAfter revision"


def test_plain_item_still_bypasses_worker(tmp_path):
    payload = telegram_payload(1, 100, text="plain")
    store, item, _, _ = staged_job(tmp_path, [payload], [], "plain")
    step = VoiceTranscriptionStep(tmp_path / "models", transcriber=FakeTranscriber())
    pipeline = ProcessingPipeline([step])
    worker = ProcessingWorker(store, pipeline)
    coordinator = ProcessingCoordinator(store, pipeline, worker)

    coordinator.submit(item)

    assert store.get_item(10, 1)["status"] == "ready"
    assert (tmp_path / "inbox" / "2026-08-09_1" / "message.md").read_text().endswith("plain")


def test_transcription_error_leaves_item_failed_in_staging(tmp_path):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    store, item, _, _ = staged_job(tmp_path, [payload], [attachment("voice", 1)])
    step = VoiceTranscriptionStep(
        tmp_path / "models",
        transcriber=FakeTranscriber(result(status="failed", text="", error="broken audio")),
    )
    pipeline = ProcessingPipeline([step])
    worker = ProcessingWorker(store, pipeline)
    coordinator = ProcessingCoordinator(store, pipeline, worker)

    worker.start()
    coordinator.submit(item)
    try:
        failed = wait_for_status(store, 1, "failed")
    finally:
        worker.stop()

    assert failed["processing_step"] == "voice-transcription"
    assert "broken audio" in failed["error"]
    assert (tmp_path / "staging" / "2026-08-09_1").is_dir()


class FakeResolver:
    def __init__(self, replacements):
        self.replacements = replacements
        self.failures = []

    def resolve(self, url):
        return self.replacements.get(url, url)


def test_url_resolution_then_cleaning_preserves_materialized_source(tmp_path):
    source = (
        "## Segment 1 — text\n\n"
        "𝗨𝘀𝗲𝗳𝘂𝗹  link: https://t.co/example"
    )
    store = CaptureStore(tmp_path)
    item = store.capture(
        10,
        1,
        source,
        received_at="2026-08-09T10:00:00+00:00",
    )
    resolver = FakeResolver(
        {
            "https://t.co/example": (
                "https://example.com/article?utm_source=social&id=7"
            )
        }
    )
    pipeline = ProcessingPipeline(
        [
            URLResolutionStep(
                timeout_seconds=1,
                retries=0,
                max_html_bytes=1024,
                resolve_all=False,
                resolver=resolver,
            ),
            TextCleaningStep(),
        ]
    )
    worker = ProcessingWorker(store, pipeline)
    coordinator = ProcessingCoordinator(store, pipeline, worker)

    worker.start()
    coordinator.submit(item)
    try:
        wait_for_status(store, 1, "ready")
    finally:
        worker.stop()

    ready = tmp_path / "inbox" / "2026-08-09_1"
    assert (ready / "source.md").read_text() == source
    assert (ready / "message.md").read_text() == (
        "---\ncategory: Other\n---\n\n"
        "## Segment 1 — text\n\n"
        "Useful link: https://example.com/article?id=7"
    )


def test_unresolved_url_does_not_fail_delivery(tmp_path):
    source = "## Segment 1 — text\n\nhttps://t.co/unavailable"
    store = CaptureStore(tmp_path)
    item = store.capture(
        10,
        1,
        source,
        received_at="2026-08-09T10:00:00+00:00",
    )
    resolver = FakeResolver({})
    resolver.failures.append(("https://t.co/unavailable", "offline"))
    step = URLResolutionStep(
        timeout_seconds=1,
        retries=0,
        max_html_bytes=1024,
        resolve_all=False,
        resolver=resolver,
    )
    pipeline = ProcessingPipeline([step])
    worker = ProcessingWorker(store, pipeline)
    coordinator = ProcessingCoordinator(store, pipeline, worker)

    worker.start()
    coordinator.submit(item)
    try:
        wait_for_status(store, 1, "ready")
    finally:
        worker.stop()

    message = (tmp_path / "inbox" / "2026-08-09_1" / "message.md").read_text()
    assert message.endswith(source)
