"""Tests for Instagram media transcription."""

import json
from types import SimpleNamespace

from info_triage.extractors.instagram import transcription


def test_no_audio_is_a_successful_empty_result(monkeypatch, tmp_path):
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"not used")
    monkeypatch.setattr(transcription, "has_audio_stream", lambda path: False)
    instance = object.__new__(transcription.FasterWhisperTranscriber)
    result = instance.transcribe(source)
    assert result.status == "no_audio"
    assert result.text == ""
    assert result.error is None


def test_transcriber_preserves_detected_language_and_text(monkeypatch, tmp_path):
    source = tmp_path / "voice.ogg"
    source.write_bytes(b"not used")
    monkeypatch.setattr(transcription, "has_audio_stream", lambda path: True)

    segment = SimpleNamespace(start=0.1, end=1.2, text="  Привет мир ")
    info = SimpleNamespace(
        language="ru",
        language_probability=0.97,
        duration=1.4,
        duration_after_vad=1.1,
    )
    model = SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([segment]), info))
    instance = object.__new__(transcription.FasterWhisperTranscriber)
    instance.model = model
    result = instance.transcribe(source, vad=True)
    assert result.status == "complete"
    assert result.text == "Привет мир"
    assert result.language == "ru"
    assert result.language_probability == 0.97
    assert result.segments[0].start == 0.1


def test_empty_model_output_is_no_speech(monkeypatch, tmp_path):
    source = tmp_path / "music.ogg"
    source.write_bytes(b"not used")
    monkeypatch.setattr(transcription, "has_audio_stream", lambda path: True)
    info = SimpleNamespace(
        language="en", language_probability=0.2, duration=3.0, duration_after_vad=0.0
    )
    instance = object.__new__(transcription.FasterWhisperTranscriber)
    instance.model = SimpleNamespace(transcribe=lambda *args, **kwargs: (iter(()), info))
    assert instance.transcribe(source).status == "no_speech"


def test_vad_setting_is_forwarded_to_faster_whisper(monkeypatch, tmp_path):
    source = tmp_path / "music.ogg"
    source.write_bytes(b"not used")
    monkeypatch.setattr(transcription, "has_audio_stream", lambda path: True)
    received = {}

    def transcribe(*args, **kwargs):
        received.update(kwargs)
        return iter(()), SimpleNamespace(language="en", language_probability=0.1, duration=1.0)

    instance = object.__new__(transcription.FasterWhisperTranscriber)
    instance.model = SimpleNamespace(transcribe=transcribe)
    instance.transcribe(source, vad=False)
    assert received["vad_filter"] is False


def test_reviewed_platform_defaults():
    assert transcription.production_defaults("Darwin", "arm64") == ("mlx", "medium")
    assert transcription.production_defaults("Linux", "x86_64") == ("faster-whisper", "small")
    assert transcription.production_defaults("Linux", "aarch64") == ("faster-whisper", "small")


def test_transcription_threads_default_to_one(monkeypatch):
    monkeypatch.delenv(transcription.TRANSCRIPTION_THREAD_ENV, raising=False)
    assert transcription.resolve_transcription_threads() == 1
    monkeypatch.setenv(transcription.TRANSCRIPTION_THREAD_ENV, "3")
    assert transcription.resolve_transcription_threads() == 3
    assert transcription.resolve_transcription_threads(2) == 2


def test_production_outputs_are_unicode_and_timestamp_free(tmp_path):
    post_dir = tmp_path / "post"
    result = transcription.TranscriptResult(
        source_file="/somewhere/01_video.mp4",
        status="complete",
        text="Привет, 世界",
        language="ru",
        language_probability=0.93,
        segments=[transcription.TranscriptSegment(1.2, 3.4, "Привет, 世界")],
    )
    transcription.write_transcript_outputs(
        post_dir,
        [result],
        backend="faster-whisper",
        model_name="small",
    )

    assert (post_dir / "transcript.txt").read_text(encoding="utf-8") == "Привет, 世界\n"
    payload = json.loads((post_dir / "transcripts/01_video.json").read_text(encoding="utf-8"))
    assert payload["text"] == "Привет, 世界"
    assert payload["model"] == "small"
    assert "segments" not in payload
    assert "start" not in payload
    assert "end" not in payload


def test_missing_audio_writes_successful_empty_artifacts(tmp_path):
    post_dir = tmp_path / "post"
    transcription.write_transcript_outputs(
        post_dir,
        [transcription.TranscriptResult(source_file="silent.mp4", status="no_audio")],
        backend="faster-whisper",
        model_name="small",
    )
    assert (post_dir / "transcript.txt").read_text(encoding="utf-8") == ""
    status = json.loads((post_dir / "transcripts/status.json").read_text(encoding="utf-8"))
    assert status["outcome"] == "complete"
    assert status["processed"][0]["status"] == "no_audio"


def test_failed_transcription_is_recorded_without_timestamps(tmp_path):
    post_dir = tmp_path / "post"
    transcription.write_transcript_outputs(
        post_dir,
        [
            transcription.TranscriptResult(
                source_file="broken.mp4",
                status="failed",
                error="RuntimeError: decode failed",
            )
        ],
        backend="faster-whisper",
        model_name="small",
    )
    payload = json.loads((post_dir / "transcripts/broken.json").read_text(encoding="utf-8"))
    status = json.loads((post_dir / "transcripts/status.json").read_text(encoding="utf-8"))
    assert payload["text"] == ""
    assert payload["error"] == "RuntimeError: decode failed"
    assert status["outcome"] == "failed"
