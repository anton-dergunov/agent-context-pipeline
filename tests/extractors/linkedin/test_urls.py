"""Tests for LinkedIn URL parsing."""

from pathlib import Path

import pytest

from info_triage.extractors.linkedin.urls import load_inputs, parse_post_url


@pytest.mark.parametrize(
    ("line", "kind", "post_id"),
    [
        (1, "share", "7491127287585665025"),
        (2, "share", "7487448227336716288"),
        (3, "ugcpost", "7491720682251194368"),
        (4, "activity", "7492266598641111040"),
        (5, "share", "7487974403637641216"),
    ],
)
def test_dataset_urls_are_supported(line, kind, post_id):
    value = (
        Path("tests/fixtures/linkedin_urls.txt").read_text(encoding="utf-8").splitlines()[line - 1]
    )
    reference = parse_post_url(value)
    assert reference.urn_type == kind
    assert reference.post_id == post_id
    assert "?" not in reference.request_url
    assert reference.request_url.startswith("https://www.linkedin.com/")


def test_inputs_are_deduplicated_by_numeric_id(tmp_path):
    url = "https://www.linkedin.com/posts/person_example-share-12345-abcd/?utm_source=share"
    path = tmp_path / "urls.txt"
    path.write_text(url + "\n" + url.replace("www.", "") + "\n", encoding="utf-8")
    assert [item.post_id for item in load_inputs([], path)] == ["12345"]


def test_unrelated_and_unsupported_urls_are_rejected():
    with pytest.raises(ValueError):
        parse_post_url("https://example.com/posts/user_example-share-123-abcd/")
    with pytest.raises(ValueError):
        parse_post_url("https://www.linkedin.com/in/example/")
