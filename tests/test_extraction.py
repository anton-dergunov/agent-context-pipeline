"""Tests for content extraction: budgets, the cache, and the records it yields."""

import json
from pathlib import Path

import pytest

from info_triage.extraction import (
    ContentExtractor,
    ExtractionSettings,
    cache_key,
    describe,
    extraction_directory_name,
)
from info_triage.models import LinkTableEntry, ProcessingJob, ProcessingResult
from info_triage.preprocessing import ContentExtractionStep


def write_extraction(directory: Path, *, status="complete", **fields):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "status.json").write_text(
        json.dumps({"status": status, **fields.pop("status_fields", {})}), encoding="utf-8"
    )
    (directory / "metadata.json").write_text(json.dumps(fields), encoding="utf-8")
    (directory / "content.md").write_text(
        "# Title\n\n- Author: Someone\n\nThe opening paragraph.\n\nAnd a second one.\n",
        encoding="utf-8",
    )
    return directory


def link(n, *, priority=4, handler="document", status="discovered", canonical=None):
    return LinkTableEntry(
        n=n,
        raw=canonical or f"https://example.com/{n}",
        canonical=canonical or f"https://example.com/{n}",
        handler=handler,
        priority=priority,
        identity=f"id{n}",
        status=status,
    )


class RecordingExtractor:
    """Stands in for the network: records what it was asked to retrieve."""

    def __init__(self, tmp_path, *, fail_on=()):
        self.tmp_path = tmp_path
        self.fail_on = set(fail_on)
        self.calls: list[str] = []

    def retrieve(self, canonical_url, handler, scratch):
        self.calls.append(canonical_url)
        if canonical_url in self.fail_on:
            raise RuntimeError("boom")
        directory = self.tmp_path / "out" / str(len(self.calls))
        return write_extraction(directory, title=f"Title {len(self.calls)}")


def run_step(tmp_path, links, *, extractor, **kwargs):
    settings = {
        "extract_budget": 5,
        "linklist_extract_budget": 2,
        "linklist_threshold": 8,
        "wall_clock_seconds": 600.0,
    }
    settings.update(kwargs)
    step = ContentExtractionStep(extractor=extractor, **settings)
    result = ProcessingResult(message_markdown="body", links=list(links))
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    outcome = step.run(ProcessingJob(1, 1, 1, "Other", tmp_path), result, workspace)
    return result, outcome


def test_budget_limits_extraction_to_the_highest_priority_links(tmp_path):
    extractor = RecordingExtractor(tmp_path)
    links = [link(n, priority=n) for n in range(1, 8)]

    result, _ = run_step(tmp_path, links, extractor=extractor)

    assert len(extractor.calls) == 5
    assert [record.link_n for record in result.extractions] == [1, 2, 3, 4, 5]


def test_a_link_list_drops_to_the_smaller_budget(tmp_path):
    """43 extracted bodies cost a fortune and cannot change the routing call."""
    extractor = RecordingExtractor(tmp_path)
    links = [link(n, priority=4) for n in range(1, 11)]

    _, _ = run_step(tmp_path, links, extractor=extractor)

    assert len(extractor.calls) == 2


def test_excluded_duplicate_and_deprioritized_rows_never_spend_the_budget(tmp_path):
    extractor = RecordingExtractor(tmp_path)
    links = [
        link(1, status="excluded"),
        link(2, status="duplicate"),
        link(3, priority=9),
        link(4),
    ]

    _, _ = run_step(tmp_path, links, extractor=extractor)

    assert extractor.calls == ["https://example.com/4"]


def test_an_extractor_failure_is_recorded_and_does_not_block_the_item(tmp_path):
    extractor = RecordingExtractor(tmp_path, fail_on=["https://example.com/1"])
    links = [link(1, priority=1), link(2, priority=2)]

    result, outcome = run_step(tmp_path, links, extractor=extractor)

    assert outcome.status == "partial"
    assert [issue.reason for issue in outcome.issues] == ["extraction-failed"]
    assert result.links[0].reason == "extraction-failed"
    # The second link still got its turn.
    assert [record.link_n for record in result.extractions] == [2]


def test_the_wall_clock_downgrades_the_rest_to_title_only(tmp_path):
    extractor = RecordingExtractor(tmp_path)
    links = [link(n, priority=n) for n in range(1, 4)]

    result, outcome = run_step(tmp_path, links, extractor=extractor, wall_clock_seconds=0.0001)

    assert outcome.status == "partial"
    assert "wall-clock-exceeded" in [issue.reason for issue in outcome.issues]
    assert len(result.extractions) < 3


def test_extraction_status_is_written_back_onto_the_link_table(tmp_path):
    extractor = RecordingExtractor(tmp_path)

    result, _ = run_step(tmp_path, [link(1)], extractor=extractor)

    assert result.links[0].extraction == "complete"
    committed = {generated.relative_path.as_posix() for generated in result.generated_files}
    assert "extracted/01-document-id1/content.md" in committed
    assert "links.json" in committed


def test_describe_reads_the_fields_the_index_renders(tmp_path):
    directory = write_extraction(
        tmp_path / "paper",
        title="Strong Model Collapse",
        authors=["A. Author", "B. Author"],
        published_at="2024/10/07",
        doi="10.48550/arXiv.2410.04840",
        abstract="We consider a supervised regression setting.",
    )

    record = describe(
        directory,
        link_n=1,
        handler="research",
        identity="arxiv:2410.04840",
        relative_directory="extracted/01-research-arxiv-2410.04840",
        canonical_url="https://arxiv.org/abs/2410.04840",
    )

    assert record.kind == "paper"
    assert record.authors == ["A. Author", "B. Author"]
    assert record.published == "2024-10-07"
    assert record.word_count == 12
    # The heading and the fact list are frontmatter already; the lead is prose.
    assert record.excerpt.startswith("The opening paragraph.")


def test_a_dict_author_is_unwrapped_and_a_missing_one_is_not_the_string_none(tmp_path):
    directory = write_extraction(tmp_path / "post", author={"name": "Maxime Labonne", "url": "…"})
    assert describe(
        directory,
        link_n=1,
        handler="linkedin",
        identity="7493943012742516737",
        relative_directory="extracted/01",
        canonical_url="https://www.linkedin.com/posts/x",
    ).authors == ["Maxime Labonne"]

    directory = write_extraction(tmp_path / "page", author=None)
    assert (
        describe(
            directory,
            link_n=1,
            handler="document",
            identity="x",
            relative_directory="extracted/01",
            canonical_url="https://example.com/",
        ).authors
        == []
    )


def test_a_failed_extraction_carries_its_reason_and_nothing_it_did_not_retrieve(tmp_path):
    directory = tmp_path / "blocked"
    directory.mkdir()
    (directory / "status.json").write_text(
        json.dumps({"status": "blocked", "reason": "access-blocked"}), encoding="utf-8"
    )

    record = describe(
        directory,
        link_n=1,
        handler="medium",
        identity="eeb660fd2db1",
        relative_directory="extracted/01",
        canonical_url="https://medium.com/x",
    )

    assert record.status == "blocked"
    assert record.reason == "access-blocked"
    assert record.retrieved is False
    assert record.title is None


class CountingExtractor(ContentExtractor):
    """A real cache around a fake extractor run."""

    runs = 0

    def _run(self, url, handler, root):
        CountingExtractor.runs += 1
        return write_extraction(root / "html" / "x", title="Cached")


def test_a_second_revision_reuses_the_cache_instead_of_re_downloading(tmp_path):
    """promote_if_current() rebuilds the item on every edit; the PDF must not be."""
    CountingExtractor.runs = 0
    extractor = CountingExtractor(ExtractionSettings(data_dir=tmp_path / "data"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    first = extractor.retrieve("https://example.com/a", "document", scratch)
    second = extractor.retrieve("https://example.com/a", "document", scratch)

    assert CountingExtractor.runs == 1
    assert first == second
    assert (first / "content.md").exists()


def test_a_cache_entry_without_its_manifest_is_re_run(tmp_path):
    """An interrupted run must not be served half-finished."""
    CountingExtractor.runs = 0
    extractor = CountingExtractor(ExtractionSettings(data_dir=tmp_path / "data"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    extractor.retrieve("https://example.com/a", "document", scratch)
    (
        tmp_path / "data" / "extraction-cache" / cache_key("https://example.com/a") / "cache.json"
    ).unlink()
    extractor.retrieve("https://example.com/a", "document", scratch)

    assert CountingExtractor.runs == 2


@pytest.mark.parametrize(
    "identity,expected",
    [
        ("arxiv:2410.04840", "01-research-arxiv-2410.04840"),
        ("", "01-research"),
        ("a/b?c", "01-research-a-b-c"),
    ],
)
def test_extraction_directory_names_are_readable_and_safe(identity, expected):
    assert extraction_directory_name(1, "research", identity) == expected
