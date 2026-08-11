"""Tests for Instagram URL parsing."""

import pytest

from info_triage.extractors.instagram.urls import shortcode_from_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.instagram.com/reels/DbfKr0UMPUL/", "DbfKr0UMPUL"),
        ("https://www.instagram.com/p/DaVncq8FmcG/?img_index=1", "DaVncq8FmcG"),
        ("https://www.instagram.com/tangoamistoso/reel/DK4OKPVOHOg/", "DK4OKPVOHOg"),
        ("C8aGZQwuB1d", "C8aGZQwuB1d"),
    ],
)
def test_shortcode_from_url(value, expected):
    assert shortcode_from_url(value) == expected


def test_rejects_unrelated_url():
    with pytest.raises(ValueError):
        shortcode_from_url("https://example.com/p/nope/")
