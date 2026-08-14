import json
import time

import pytest

from info_triage.extractors.media.transcription import TranscriptResult
from info_triage.models import (
    AttachmentSpec,
    DownloadedAttachment,
    ProcessingJob,
    ProcessingResult,
)
from info_triage.preprocessing import (
    LinkDiscoveryStep,
    TextCleaningStep,
    URLResolutionStep,
    VoiceTranscriptionStep,
)
from info_triage.processing import ProcessingCoordinator, ProcessingPipeline, ProcessingWorker
from info_triage.rendering import render_capture_payloads
from info_triage.storage import CaptureStore
from info_triage.utilities.url_resolution import LinkResolution


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
    source = (job.path / "capture" / "message.md").read_text(encoding="utf-8")
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
def test_transcription_failure_returns_declared_failure(tmp_path, transcript, expected):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    _, _, job, processing_result = staged_job(tmp_path, [payload], [attachment("voice", 1)])
    step = VoiceTranscriptionStep(tmp_path / "models", transcriber=FakeTranscriber(transcript))

    outcome = step.run(job, processing_result, tmp_path / "workspace")

    assert outcome.status == "failed"
    assert outcome.issues[0].reason == "transcription-failed"
    assert expected in outcome.issues[0].message


def test_missing_download_returns_declared_failure(tmp_path):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    _, _, job, processing_result = staged_job(
        tmp_path, [payload], [attachment("voice", 1, data=None)]
    )
    step = VoiceTranscriptionStep(tmp_path / "models", transcriber=FakeTranscriber())

    outcome = step.run(job, processing_result, tmp_path / "workspace")

    assert outcome.status == "failed"
    assert outcome.issues[0].reason == "attachment-unavailable"
    assert "download failed" in outcome.issues[0].message


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

    message = (tmp_path / "inbox" / "2026-08-09_1" / "capture" / "message.md").read_text()
    assert message == (
        "---\ncategory: Life\n---\n\n"
        "## Segment 1 — voice\n\nAfter revision"
    )
    source = (tmp_path / "inbox" / "2026-08-09_1" / "capture" / "source.md").read_text()
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
    assert (tmp_path / "inbox" / "2026-08-09_1" / "capture" / "message.md").read_text().endswith("plain")


def test_transcription_error_is_logged_but_item_is_delivered(tmp_path):
    payload = telegram_payload(
        1,
        100,
        voice={"file_id": "voice-1", "file_unique_id": "unique-voice-1", "duration": 1},
    )
    store, item, _, _ = staged_job(
        tmp_path,
        [payload],
        [attachment("voice", 1)],
        "𝗞𝗲𝗲𝗽 this raw note",
    )
    step = VoiceTranscriptionStep(
        tmp_path / "models",
        transcriber=FakeTranscriber(result(status="failed", text="", error="broken audio")),
    )
    pipeline = ProcessingPipeline([step, TextCleaningStep()])
    worker = ProcessingWorker(store, pipeline)
    coordinator = ProcessingCoordinator(store, pipeline, worker)

    worker.start()
    coordinator.submit(item)
    try:
        ready = wait_for_status(store, 1, "ready")
    finally:
        worker.stop()

    assert ready["processing_step"] is None
    assert ready["error"] is None
    item_path = tmp_path / "inbox" / "2026-08-09_1"
    assert item_path.is_dir()
    assert (item_path / "capture" / "attachments" / "01-voice.ogg").read_bytes() == b"media"
    assert (item_path / "capture" / "message.md").read_text().endswith("Keep this raw note")
    stats = {row["processor"]: row for row in store.processor_statistics()}
    assert stats["voice-transcription"]["failed"] == 1
    assert stats["voice-transcription"]["reasons"][0]["reason"] == "transcription-failed"
    assert stats["text-cleaning"]["succeeded"] == 1


class FakeResolver:
    def __init__(self, replacements):
        self.replacements = replacements
        self.failures = []
        # Mirrors URLResolver: a URL is fetched at most once per process.
        self.link_results = {}

    def resolve(self, url):
        return self.replacements.get(url, url)

    def resolve_link(self, url):
        if url not in self.link_results:
            self.link_results[url] = LinkResolution(self.resolve(url), "Resolved article")
        return self.link_results[url]


def test_cleaning_then_url_resolution_preserves_materialized_source(tmp_path):
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
            TextCleaningStep(),
            URLResolutionStep(
                timeout_seconds=1,
                retries=0,
                max_html_bytes=1024,
                max_pdf_bytes=2048,
                resolve_all=False,
                resolve_budget=10,
                resolver=resolver,
            ),
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
    assert (ready / "capture" / "source.md").read_text() == source
    # Cleaning runs first, so the resolved destination is inserted afterwards and keeps
    # its tracking parameters. Canonicalization moves into link discovery.
    assert (ready / "capture" / "message.md").read_text() == (
        "---\ncategory: Other\n---\n\n"
        "## Segment 1 — text\n\n"
        "Useful link: [Resolved article](https://example.com/article?utm_source=social&id=7)"
    )


def test_unresolved_url_does_not_fail_delivery(tmp_path):
    source = "## Segment 1 — text\n\n𝗞𝗲𝗲𝗽 https://t.co/unavailable"
    store = CaptureStore(tmp_path)
    item = store.capture(
        10,
        1,
        source,
        received_at="2026-08-09T10:00:00+00:00",
    )
    class FailingResolver(FakeResolver):
        def resolve_link(self, url):
            self.failures.append((url, "offline"))
            return LinkResolution(url, None, "request-error", "offline")

    resolver = FailingResolver({})
    step = URLResolutionStep(
        timeout_seconds=1,
        retries=0,
        max_html_bytes=1024,
        max_pdf_bytes=2048,
        resolve_all=False,
        resolve_budget=10,
        resolver=resolver,
    )
    pipeline = ProcessingPipeline([TextCleaningStep(), step])
    worker = ProcessingWorker(store, pipeline)
    coordinator = ProcessingCoordinator(store, pipeline, worker)

    worker.start()
    coordinator.submit(item)
    try:
        wait_for_status(store, 1, "ready")
    finally:
        worker.stop()

    message = (tmp_path / "inbox" / "2026-08-09_1" / "capture" / "message.md").read_text()
    assert message.endswith("## Segment 1 — text\n\nKeep https://t.co/unavailable")
    stats_by_name = {row["processor"]: row for row in store.processor_statistics()}
    stats = stats_by_name["url-resolution"]
    assert stats["runs"] == 1
    assert stats["partial"] == 1
    assert stats["reasons"] == [
        {
            "processor": "url-resolution",
            "outcome": "partial",
            "reason": "request-error",
            "occurrences": 1,
        }
    ]
    assert stats_by_name["text-cleaning"]["succeeded"] == 1


def link_table(job, result, *, resolver=None, resolve_budget=40, workspace=None):
    """Run discovery, and optionally resolution, over one staged item."""
    workspace = workspace or job.path.parent / "workspace"
    discovery_space = workspace / "discovery"
    discovery_space.mkdir(parents=True)
    LinkDiscoveryStep().run(job, result, discovery_space)
    if resolver is not None:
        resolution_space = workspace / "resolution"
        resolution_space.mkdir(parents=True)
        step = URLResolutionStep(
            timeout_seconds=1,
            retries=0,
            max_html_bytes=1024,
            max_pdf_bytes=2048,
            resolve_all=False,
            resolve_budget=resolve_budget,
            resolver=resolver,
        )
        step.run(job, result, resolution_space)
    return result.links


def test_hidden_hyperlinks_survive_capture_and_reach_the_link_table(tmp_path):
    payload = telegram_payload(
        1,
        100,
        text="Diffusion explainer\nGithub",
        entities=[
            {
                "type": "text_link",
                "offset": 0,
                "length": 19,
                "url": "https://poloclub.github.io/diffusion-explainer/",
            },
            {
                "type": "text_link",
                "offset": 20,
                "length": 6,
                "url": "https://github.com/poloclub/diffusion-explainer",
            },
        ],
    )
    store = CaptureStore(tmp_path)
    item = store.capture(
        10,
        1,
        render_capture_payloads([payload]),
        received_at="2026-08-09T10:00:00+00:00",
        telegram_payload=payload,
    )
    pipeline = ProcessingPipeline(
        [
            TextCleaningStep(),
            LinkDiscoveryStep(),
            URLResolutionStep(
                timeout_seconds=1,
                retries=0,
                max_html_bytes=1024,
                max_pdf_bytes=2048,
                resolve_all=False,
                resolve_budget=40,
                resolver=FakeResolver({}),
            ),
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
    # The destinations Telegram hides from message text are in the body itself,
    # both before and after transformation, and are not re-titled by resolution.
    assert "https://poloclub.github.io/diffusion-explainer/" in (
        ready / "capture" / "source.md"
    ).read_text()
    assert (ready / "capture" / "message.md").read_text().endswith(
        "## Segment 1 — text\n\n"
        "[Diffusion explainer](https://poloclub.github.io/diffusion-explainer/)\n"
        "[Github](https://github.com/poloclub/diffusion-explainer)"
    )
    links = json.loads((ready / "links.json").read_text())["links"]
    assert [(entry["n"], entry["canonical"], entry["status"]) for entry in links] == [
        (1, "https://poloclub.github.io/diffusion-explainer/", "resolved"),
        (2, "https://github.com/poloclub/diffusion-explainer", "resolved"),
    ]
    assert {row["processor"] for row in store.processor_statistics()} >= {"link-discovery"}


def test_resolution_updates_the_table_and_reroutes_expanded_shorteners(tmp_path):
    payload = telegram_payload(1, 100, text="https://lnkd.in/abc and https://example.com/x")
    _, _, job, result = staged_job(tmp_path, [payload], None, render_capture_payloads([payload]))
    resolver = FakeResolver({"https://lnkd.in/abc": "https://arxiv.org/abs/2305.03509?utm_x=1"})

    table = link_table(job, result, resolver=resolver)

    # The shortener hid a paper, so both the handler and the rank discovery could
    # only guess offline are corrected here.
    assert [
        (entry.canonical, entry.handler, entry.priority, entry.status) for entry in table
    ] == [
        ("https://arxiv.org/abs/2305.03509", "research", 1, "resolved"),
        ("https://example.com/x", "document", 4, "resolved"),
    ]
    assert all(entry.title == "Resolved article" for entry in table)


def test_links_beyond_the_resolve_budget_are_recorded_not_dropped(tmp_path):
    text = "\n".join(f"https://example.com/{index}" for index in range(4))
    payload = telegram_payload(1, 100, text=text)
    _, _, job, result = staged_job(tmp_path, [payload], None, render_capture_payloads([payload]))

    table = link_table(job, result, resolver=FakeResolver({}), resolve_budget=2)

    assert [entry.status for entry in table] == [
        "resolved",
        "resolved",
        "skipped",
        "skipped",
    ]
    assert {entry.reason for entry in table if entry.status == "skipped"} == {
        "resolve-budget-exhausted"
    }
    # Resolved links are titled in the body; over-budget ones stay bare rather
    # than spending network the item was not granted.
    assert "[Resolved article](https://example.com/0)" in result.message_markdown
    assert "\nhttps://example.com/3" in result.message_markdown


def test_two_links_resolving_to_one_target_collapse_to_a_duplicate(tmp_path):
    payload = telegram_payload(1, 100, text="https://lnkd.in/abc\nhttps://t.co/def")
    _, _, job, result = staged_job(tmp_path, [payload], None, render_capture_payloads([payload]))
    resolver = FakeResolver(
        {
            "https://lnkd.in/abc": "https://example.com/article",
            "https://t.co/def": "https://example.com/article",
        }
    )

    table = link_table(job, result, resolver=resolver)

    assert [(entry.n, entry.status, entry.duplicate_of) for entry in table] == [
        (1, "resolved", None),
        (2, "duplicate", 1),
    ]


def test_unreadable_payloads_still_deliver_the_visible_links(tmp_path):
    payload = telegram_payload(1, 100, text="https://example.com/x")
    _, _, job, result = staged_job(tmp_path, [payload], None, render_capture_payloads([payload]))
    (job.path / "capture" / "telegram.json").write_text("not json", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    outcome = LinkDiscoveryStep().run(job, result, workspace)

    assert outcome.status == "partial"
    assert outcome.issues[0].reason == "invalid-input"
    assert [entry.canonical for entry in result.links] == ["https://example.com/x"]
