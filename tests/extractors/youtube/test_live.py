"""Opt-in end-to-end checks against the representative public corpus."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from info_triage.extractors.media.ocr import make_engine
from info_triage.extractors.media.runtime import apply_runtime_threads
from info_triage.extractors.media.transcription import make_transcriber
from info_triage.extractors.youtube.extractor import ExtractionOptions, YouTubeExtractor
from info_triage.extractors.youtube.runner import ManagedYtDlp, RunnerSettings
from info_triage.extractors.youtube.urls import load_inputs

pytestmark = pytest.mark.skipif(
    os.environ.get("YOUTUBE_LIVE") != "1",
    reason="set YOUTUBE_LIVE=1 to run network/model/media extraction",
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/youtube_urls.txt"


def test_representative_youtube_corpus(tmp_path):
    references = load_inputs([], FIXTURE)
    assert len(references) == 10
    apply_runtime_threads(1)

    runner = ManagedYtDlp(
        RunnerSettings(
            tool_dir=tmp_path / "tools/yt-dlp",
            channel="nightly",
            max_attempts=3,
            retry_backoff_seconds=5,
        )
    )
    extractor = YouTubeExtractor(
        runner,
        ExtractionOptions(output_dir=tmp_path / "output"),
        ocr_engine_factory=lambda: make_engine(
            "rapidocr", None, tmp_path / "ocr-models", threads=1
        ),
        transcriber_factory=lambda: make_transcriber(
            "faster-whisper", "small", tmp_path / "whisper-models", 1
        ),
    )
    try:
        outputs = [extractor.extract(reference) for reference in references]
    finally:
        extractor.close()

    for reference, output in zip(references, outputs):
        metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        comments = json.loads((output / "comments.json").read_text(encoding="utf-8"))
        transcript = json.loads((output / "transcript.json").read_text(encoding="utf-8"))
        status = json.loads((output / "status.json").read_text(encoding="utf-8"))
        assert metadata["video_id"] == reference.video_id
        assert metadata["title"]
        assert comments["parent_count"] <= 5
        assert comments["reply_count"] <= 10
        assert all(len(parent["replies"]) <= 2 for parent in comments["comments"])
        assert transcript["status"] in {
            "complete",
            "no_speech",
            "unavailable",
            "rejected",
            "failed",
        }
        assert status["stages"]["comments"] == "complete"

    for output in outputs[:3]:
        assert any((output / "media").iterdir())
        assert (output / "ocr/video.ocr.json").exists()
        assert (output / "transcript.json").exists()

    for output in outputs[3:]:
        assert not (output / "media").exists()
