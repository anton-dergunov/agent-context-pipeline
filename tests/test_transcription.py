from pathlib import Path
from types import SimpleNamespace

from instagram_extractor import transcription


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
    info = SimpleNamespace(language="en", language_probability=0.2, duration=3.0, duration_after_vad=0.0)
    instance = object.__new__(transcription.FasterWhisperTranscriber)
    instance.model = SimpleNamespace(transcribe=lambda *args, **kwargs: (iter(()), info))
    assert instance.transcribe(source).status == "no_speech"

