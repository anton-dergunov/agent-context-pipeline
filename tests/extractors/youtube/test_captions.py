import json

from info_triage.extractors.youtube.captions import (
    CaptionTrack,
    parse_json3,
    select_caption_track,
)

JSON3 = [{"ext": "json3", "url": "https://example.test/caption"}]


def test_manual_original_language_is_preferred_over_automatic_and_translations():
    track = select_caption_track(
        {
            "language": "en-US",
            "subtitles": {"fr": JSON3, "en": JSON3, "live_chat": JSON3},
            "automatic_captions": {"en-orig": JSON3, "es": JSON3},
        }
    )
    assert track == CaptionTrack("en", "youtube_manual")


def test_only_original_automatic_caption_is_accepted():
    assert select_caption_track(
        {
            "language": "es",
            "subtitles": {},
            "automatic_captions": {"en": JSON3, "es": JSON3, "es-orig": JSON3},
        }
    ) == CaptionTrack("es-orig", "youtube_automatic")
    assert (
        select_caption_track(
            {"language": "en", "subtitles": {}, "automatic_captions": {"es": JSON3}}
        )
        is None
    )


def test_caption_quality_gate_accepts_substantial_span(tmp_path):
    path = tmp_path / "captions.json3"
    path.write_text(
        json.dumps(
            {
                "events": [
                    {"tStartMs": 1000, "dDurationMs": 3000, "segs": [{"utf8": "Hello "}]},
                    {
                        "tStartMs": 9000,
                        "dDurationMs": 2000,
                        "segs": [{"utf8": "from captions today"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    result = parse_json3(path, CaptionTrack("en-orig", "youtube_automatic"), 30)
    assert result.status == "complete"
    assert result.text == "Hello from captions today"
    assert result.cue_span_ratio >= 0.25


def test_caption_quality_gate_rejects_tiny_or_truncated_tracks(tmp_path):
    path = tmp_path / "captions.json3"
    path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "tStartMs": 0,
                        "dDurationMs": 1000,
                        "segs": [{"utf8": "Only a tiny fragment of speech"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = parse_json3(path, CaptionTrack("en-orig", "youtube_automatic"), 60)
    assert result.status == "rejected"
    assert result.reason == "caption_span_too_short"
    assert result.text == ""


def test_caption_quality_gate_rejects_invalid_structure(tmp_path):
    path = tmp_path / "captions.json3"
    path.write_text("[]\n", encoding="utf-8")
    result = parse_json3(path, CaptionTrack("en", "youtube_manual"), 60)
    assert result.status == "rejected"
    assert result.reason == "invalid_caption_structure"
