from pathlib import Path

import pytest

from info_triage.extractors.youtube.urls import classify_video, load_inputs, parse_video_url


@pytest.mark.parametrize(
    ("url", "video_id", "explicit_short"),
    [
        ("https://www.youtube.com/watch?v=giNWPfAen8g", "giNWPfAen8g", False),
        ("https://www.youtube.com/shorts/WZxbzEpdjlU", "WZxbzEpdjlU", True),
        ("https://youtu.be/59CAwbGTHLE?t=3", "59CAwbGTHLE", False),
        ("m.youtube.com/watch?v=_X82NsOJguc", "_X82NsOJguc", False),
    ],
)
def test_supported_video_urls(url, video_id, explicit_short):
    result = parse_video_url(url)
    assert result.video_id == video_id
    assert result.explicit_short is explicit_short
    assert result.canonical_url == f"https://www.youtube.com/watch?v={video_id}"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/watch?v=giNWPfAen8g&list=PL123",
        "https://www.youtube.com/@channel",
        "https://example.com/watch?v=giNWPfAen8g",
    ],
)
def test_collections_and_non_youtube_urls_are_rejected(url):
    with pytest.raises(ValueError):
        parse_video_url(url)


def test_fixture_loads_all_ten_labeled_urls_once():
    fixture = Path(__file__).parents[2] / "fixtures/youtube_urls.txt"
    references = load_inputs([], fixture)
    assert len(references) == 10
    assert [item.video_id for item in references[:3]] == [
        "_-6hzpk9cG8",
        "RCIEEQxRer8",
        "WZxbzEpdjlU",
    ]


def test_short_classification_uses_url_then_portrait_heuristic_not_duration_alone():
    short = parse_video_url("https://www.youtube.com/shorts/WZxbzEpdjlU")
    watch = parse_video_url("https://www.youtube.com/watch?v=59CAwbGTHLE")
    assert classify_video(short, {"duration": 30, "aspect_ratio": 0.56}) == (
        "short",
        "shorts_url",
    )
    assert classify_video(watch, {"duration": 62, "aspect_ratio": 1.78}) == (
        "video",
        "default_video",
    )
    assert classify_video(watch, {"duration": 120, "width": 720, "height": 1280}) == (
        "short",
        "portrait_duration_heuristic",
    )
    assert classify_video(watch, {}, "short") == ("short", "explicit_override")
