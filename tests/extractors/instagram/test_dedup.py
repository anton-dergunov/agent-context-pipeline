"""Tests for Instagram OCR frame deduplication."""

from info_triage.extractors.instagram.dedup import merge_frames
from info_triage.extractors.instagram.models import OCRFrame, OCRLine

BOX = (0.1, 0.2, 0.5, 0.1)


def frame(time, text):
    return OCRFrame(
        time_seconds=time, frame_number=round(time * 10), lines=[OCRLine(text, 0.9, BOX)]
    )


def test_progressive_reveal_keeps_only_complete_text():
    segments = merge_frames(
        [
            frame(0, "H"),
            frame(0.1, "HE"),
            frame(0.2, "HEL"),
            frame(0.3, "HELL"),
            frame(0.4, "HELLO"),
        ]
    )
    assert [item.text for item in segments] == ["HELLO"]
    assert segments[0].observations == 5


def test_repeated_text_is_collapsed_across_time():
    segments = merge_frames(
        [
            frame(0, "Useful travel tip"),
            frame(1, "Useful travel tip"),
            frame(10, "Useful travel tip"),
        ]
    )
    assert [item.text for item in segments] == ["Useful travel tip"]
    assert segments[0].observations == 3


def test_ocr_variants_choose_the_consensus_spelling():
    segments = merge_frames(
        [
            frame(0.0, "Facundo Pinero"),
            frame(0.1, "Facundo Piñero"),
            frame(0.2, "Facundo Piñero"),
            frame(0.3, "Facundo Piero"),
        ]
    )
    assert [item.text for item in segments] == ["Facundo Piñero"]
    assert segments[0].observations == 4


def test_distinct_subtitles_at_same_location_remain_distinct():
    segments = merge_frames(
        [frame(0.0, "First useful idea"), frame(0.1, "Completely different advice")]
    )
    assert [item.text for item in segments] == ["First useful idea", "Completely different advice"]
