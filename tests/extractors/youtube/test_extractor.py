import json
from types import SimpleNamespace

from info_triage.extractors.youtube.captions import CaptionResult, CaptionSegment
from info_triage.extractors.youtube.extractor import (
    ExtractionOptions,
    YouTubeExtractor,
    sanitize_raw_metadata,
    thread_comments,
)
from info_triage.extractors.youtube.runner import CommandResult, YtDlpError
from info_triage.extractors.youtube.urls import parse_video_url


class FakeRunner:
    def __init__(self, metadata, comments=()):
        self.metadata = metadata
        self.comments = list(comments)
        self.events = []
        self.commands = []

    def run_json(self, arguments, **_kwargs):
        self.commands.append(arguments)
        if "--write-comments" in arguments:
            return {"comments": self.comments}
        return self.metadata

    def run(self, arguments, **_kwargs):
        self.commands.append(arguments)
        if "--output" in arguments:
            template = arguments[arguments.index("--output") + 1]
            if "%(ext)s" in template:
                path = template.replace("%(ext)s", "mp4")
                from pathlib import Path

                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_bytes(b"media")
        return CommandResult("", "", 0, "test-version")

    def current_version(self):
        return "test-version"


class FakeTranscriber:
    backend = "fake-whisper"
    model_name = "small"

    def __init__(self):
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return SimpleNamespace(
            status="complete",
            language="en",
            language_probability=0.9,
            text="Locally recognized speech",
            error=None,
        )


class FakeEngine:
    name = "fake-ocr"


def metadata(**overrides):
    value = {
        "id": "WZxbzEpdjlU",
        "title": "A title",
        "description": "Description",
        "duration": 30,
        "aspect_ratio": 0.56,
        "language": "en",
        "subtitles": {},
        "automatic_captions": {},
        "comment_count": 20,
    }
    value.update(overrides)
    return value


def test_comment_selection_preserves_parent_order_and_budgets_replies():
    values = [
        {"id": "p1", "parent": "root", "text": "first"},
        {"id": "r1", "parent": "p1", "text": "one"},
        {"id": "r2", "parent": "p1", "text": "two"},
        {"id": "r3", "parent": "p1", "text": "three"},
        {"id": "p2", "parent": "root", "text": "second"},
        {"id": "r4", "parent": "p2", "text": "four"},
    ]
    result = thread_comments(values, max_parents=2, max_replies=3, max_replies_per_thread=2)
    assert [item["id"] for item in result["comments"]] == ["p1", "p2"]
    assert [item["id"] for item in result["comments"][0]["replies"]] == ["r1", "r2"]
    assert [item["id"] for item in result["comments"][1]["replies"]] == ["r4"]
    assert result["reply_count"] == 3


def test_raw_metadata_keeps_shape_but_redacts_access_values():
    raw = {
        "id": "WZxbzEpdjlU",
        "title": "safe",
        "formats": [
            {
                "format_id": "18",
                "url": "https://signed.example/video?token=secret",
                "http_headers": {"Cookie": "secret"},
            }
        ],
    }
    sanitized = sanitize_raw_metadata(raw)
    assert sanitized["id"] == raw["id"]
    assert sanitized["formats"][0]["format_id"] == "18"
    assert sanitized["formats"][0]["url"] == "[redacted]"
    assert sanitized["formats"][0]["http_headers"] == "[redacted]"


def test_short_without_usable_captions_reuses_whisper_and_exact_ocr_defaults(monkeypatch, tmp_path):
    runner = FakeRunner(metadata())
    transcriber = FakeTranscriber()
    ocr_calls = []

    def fake_ocr(source, output, engine, **kwargs):
        ocr_calls.append((source, engine, kwargs))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "source_file": source.name,
                    "engine": engine.name,
                    "llm_ready_segments": [{"text": "Screen text"}],
                }
            ),
            encoding="utf-8",
        )
        output.with_suffix(".txt").write_text("Screen text\n", encoding="utf-8")

    monkeypatch.setattr("info_triage.extractors.youtube.extractor.ocr_video", fake_ocr)
    extractor = YouTubeExtractor(
        runner,
        ExtractionOptions(output_dir=tmp_path),
        ocr_engine_factory=FakeEngine,
        transcriber_factory=lambda: transcriber,
    )
    output = extractor.extract(parse_video_url("https://www.youtube.com/shorts/WZxbzEpdjlU"))

    transcript = json.loads((output / "transcript.json").read_text(encoding="utf-8"))
    assert transcript["source"] == "local_whisper"
    assert transcript["text"] == "Locally recognized speech"
    assert len(transcriber.calls) == 1
    assert ocr_calls[0][2] == {
        "mode": "sample",
        "sample_fps": 3.0,
        "max_height": 800,
        "batch_size": 8,
    }
    assert "TRANSCRIPT (local_whisper)" in (output / "llm_input.txt").read_text(encoding="utf-8")
    assert (output / "media/video.mp4").exists()


def test_good_youtube_caption_is_the_only_short_transcript(monkeypatch, tmp_path):
    runner = FakeRunner(metadata(automatic_captions={"en-orig": [{"ext": "json3"}]}))
    transcriber = FakeTranscriber()
    caption = CaptionResult(
        status="complete",
        source="youtube_automatic",
        language="en-orig",
        text="A complete platform caption",
        segments=(CaptionSegment(0, 20, "A complete platform caption"),),
        visible_characters=24,
        cue_span_ratio=0.67,
    )
    monkeypatch.setattr(YouTubeExtractor, "_download_caption", lambda *_args: caption)
    extractor = YouTubeExtractor(
        runner,
        ExtractionOptions(output_dir=tmp_path, skip_ocr=True),
        transcriber_factory=lambda: transcriber,
    )
    output = extractor.extract(parse_video_url("https://www.youtube.com/shorts/WZxbzEpdjlU"))
    assert not transcriber.calls
    assert (output / "transcript.txt").read_text(encoding="utf-8") == (
        "A complete platform caption\n"
    )
    readable = (output / "llm_input.txt").read_text(encoding="utf-8")
    assert readable.count("TRANSCRIPT") == 1
    assert "youtube_automatic" in readable


def test_normal_video_never_downloads_media_or_runs_local_asr(tmp_path):
    normal_id = "59CAwbGTHLE"
    runner = FakeRunner(metadata(id=normal_id, duration=62, aspect_ratio=1.78))
    transcriber = FakeTranscriber()
    extractor = YouTubeExtractor(
        runner,
        ExtractionOptions(output_dir=tmp_path),
        transcriber_factory=lambda: transcriber,
    )
    output = extractor.extract(parse_video_url(f"https://www.youtube.com/watch?v={normal_id}"))
    normalized = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert normalized["kind"] == "video"
    assert normalized["media"] == []
    assert not (output / "media").exists()
    assert not transcriber.calls
    assert all("--format" not in command for command in runner.commands)


def test_comment_failure_is_partial_and_keeps_metadata(monkeypatch, tmp_path):
    class CommentsFail(FakeRunner):
        def run_json(self, arguments, **kwargs):
            if "--write-comments" in arguments:
                raise RuntimeError("comments blocked")
            return super().run_json(arguments, **kwargs)

    runner = CommentsFail(metadata(duration=600, aspect_ratio=1.78))
    extractor = YouTubeExtractor(runner, ExtractionOptions(output_dir=tmp_path))
    output = extractor.extract(parse_video_url("https://www.youtube.com/watch?v=WZxbzEpdjlU"))
    status = json.loads((output / "status.json").read_text(encoding="utf-8"))
    assert status["outcome"] == "partial"
    assert status["stages"]["comments"] == "failed"
    assert (output / "metadata.json").exists()


def test_local_asr_exception_is_partial_and_does_not_discard_artifacts(tmp_path):
    class BrokenTranscriber(FakeTranscriber):
        def transcribe(self, path, **kwargs):
            raise RuntimeError("decoder unavailable")

    runner = FakeRunner(metadata())
    extractor = YouTubeExtractor(
        runner,
        ExtractionOptions(output_dir=tmp_path, skip_ocr=True),
        transcriber_factory=BrokenTranscriber,
    )
    output = extractor.extract(parse_video_url("https://www.youtube.com/shorts/WZxbzEpdjlU"))
    status = json.loads((output / "status.json").read_text(encoding="utf-8"))
    transcript = json.loads((output / "transcript.json").read_text(encoding="utf-8"))
    assert status["outcome"] == "partial"
    assert status["stages"]["transcript"] == "failed"
    assert transcript["source"] == "local_whisper"
    assert (output / "metadata_raw.json").exists()


def test_skip_comments_avoids_comment_request(tmp_path):
    runner = FakeRunner(metadata(duration=600, aspect_ratio=1.78))
    extractor = YouTubeExtractor(
        runner,
        ExtractionOptions(output_dir=tmp_path, skip_comments=True),
    )
    output = extractor.extract(parse_video_url("https://www.youtube.com/watch?v=WZxbzEpdjlU"))
    status = json.loads((output / "status.json").read_text(encoding="utf-8"))
    assert status["stages"]["comments"] == "skipped"
    assert all("--write-comments" not in command for command in runner.commands)


def test_separate_audio_failure_retains_video_for_ocr_and_marks_partial(tmp_path):
    class AudioFailRunner(FakeRunner):
        def run(self, arguments, **kwargs):
            selected_format = (
                arguments[arguments.index("--format") + 1] if "--format" in arguments else ""
            )
            if selected_format.startswith("b[") or selected_format == "ba":
                raise YtDlpError("format unavailable", kind="unknown")
            return super().run(arguments, **kwargs)

    runner = AudioFailRunner(metadata())
    extractor = YouTubeExtractor(
        runner,
        ExtractionOptions(output_dir=tmp_path, skip_ocr=True),
        transcriber_factory=FakeTranscriber,
    )
    output = extractor.extract(parse_video_url("https://www.youtube.com/shorts/WZxbzEpdjlU"))
    status = json.loads((output / "status.json").read_text(encoding="utf-8"))
    assert status["outcome"] == "partial"
    assert status["stages"]["media"] == "partial"
    assert (output / "media/video.mp4").exists()
