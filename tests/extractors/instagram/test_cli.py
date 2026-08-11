"""Tests for the Instagram extraction CLI."""

import pytest

from info_triage.extractors.instagram.cli import build_parser, main


def test_transcription_cli_choices_and_vad_toggle():
    args = build_parser().parse_args(
        [
            "SHORTCODE",
            "--transcription-backend",
            "faster-whisper",
            "--transcription-model",
            "small",
            "--no-transcription-vad",
        ]
    )
    assert args.transcription_backend == "faster-whisper"
    assert args.transcription_model == "small"
    assert args.transcription_vad is False


def test_transcription_threads_must_be_positive():
    with pytest.raises(SystemExit):
        main(["SHORTCODE", "--transcription-threads", "0"])
