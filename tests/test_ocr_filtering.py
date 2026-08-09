import pytest

from instagram_extractor.engines import RapidOCREngine
from instagram_extractor.ocr import filter_thresholds


@pytest.mark.parametrize(
    "fps, expected",
    [
        # The thresholds were previously hardcoded to 3 observations, with the
        # short-text one scaled by source fps. These reproduce that exactly on
        # real footage, so switching to sampling does not silently change the
        # meaning of the filter at full rate.
        (30.0, (3, 8)),
        (25.0, (3, 7)),
        (24.0, (3, 6)),
    ],
)
def test_full_rate_thresholds_match_previous_behaviour(fps, expected):
    assert filter_thresholds(fps) == expected


@pytest.mark.parametrize("fps", [3.0, 2.0, 1.0])
def test_sampled_rates_keep_a_usable_floor(fps):
    """At 3 fps a flat "3 observations" would demand a full second on screen."""
    minimum, short = filter_thresholds(fps)
    assert minimum == 2
    assert short == 3


def test_zero_fps_is_safe():
    assert filter_thresholds(0.0) == (3, 3)


def test_thresholds_are_monotonic():
    rates = [1, 2, 3, 6, 12, 24, 30, 60]
    minimums = [filter_thresholds(float(r))[0] for r in rates]
    assert minimums == sorted(minimums)


class _Box:
    """Minimal stand-in for a rapidocr result."""

    def __init__(self, boxes, txts, scores):
        self.boxes = boxes
        self.txts = txts
        self.scores = scores


def test_polygon_is_converted_to_normalized_bottom_left_bbox():
    # PP-OCR returns pixel polygons with y growing downward; the project
    # convention is normalized with y growing upward, matching Apple Vision.
    result = _Box(
        boxes=[[(10, 20), (110, 20), (110, 60), (10, 60)]],
        txts=["hello"],
        scores=[0.9],
    )
    lines = RapidOCREngine._lines(result, width=200, height=100)
    assert len(lines) == 1
    x, y, w, h = lines[0].bbox
    assert x == pytest.approx(0.05)      # 10/200
    assert w == pytest.approx(0.5)       # (110-10)/200
    assert h == pytest.approx(0.4)       # (60-20)/100
    assert y == pytest.approx(0.4)       # 1 - 60/100
    assert lines[0].text == "hello"
    assert lines[0].confidence == pytest.approx(0.9)


def test_blank_recognitions_are_dropped():
    result = _Box(
        boxes=[[(0, 0), (10, 0), (10, 10), (0, 10)], [(0, 0), (10, 0), (10, 10), (0, 10)]],
        txts=["   ", "real"],
        scores=[0.99, 0.5],
    )
    lines = RapidOCREngine._lines(result, width=100, height=100)
    assert [line.text for line in lines] == ["real"]
