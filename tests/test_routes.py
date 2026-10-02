"""Routes: separate bots, separate pipelines, separate inbox trees.

A route is a capture-time pipeline switch. These cover the three things that only
break once there is more than one of them: identity that no longer fits
`(chat_id, message_id)`, a pipeline chosen per item, and an item that moves
between routes after it was captured.
"""

from __future__ import annotations

import json

import pytest
from conftest import make_store

from info_triage.processing import ProcessingCoordinator, ProcessingPipeline, ProcessingWorker
from info_triage.telegram_bot import requested_route

ROUTES = ("info", "job", "clip", "lang")


class RecordingStep:
    """A step that records which items it ran on and changes nothing."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.seen: list[tuple[str, str]] = []

    def applies(self, job) -> bool:
        return True

    def run(self, job, result, workspace):
        self.seen.append((job.route, job.path.name))
        return None


def test_two_bots_receiving_the_same_chat_and_message_id_make_two_items(store):
    """A private chat's id is the user's own, and message ids restart per bot."""
    first = store.capture("info", 10, 1, "an article", received_at="2026-08-09T10:00:00+00:00")
    second = store.capture("job", 10, 1, "a posting", received_at="2026-08-09T10:00:00+00:00")

    assert first.path != second.path
    assert first.path.parent.name == "info"
    assert second.path.parent.name == "job"
    assert (first.path / "capture" / "message.md").read_text() == "an article"
    assert (second.path / "capture" / "message.md").read_text() == "a posting"


def test_item_numbers_run_per_route_and_ignore_other_routes(store):
    """`<date>_<n>` means the nth item this route received, whatever else arrived."""
    numbers = []
    for index, route in enumerate(("info", "job", "info", "lang", "info"), 1):
        item = store.capture(route, 10, index, "text", received_at="2026-08-09T10:00:00+00:00")
        numbers.append((route, item.local_id))

    assert numbers == [("info", 1), ("job", 1), ("info", 2), ("lang", 1), ("info", 3)]


def test_an_http_capture_and_a_telegram_capture_never_share_a_directory(store):
    """The counter is one sequence per route, whichever transport draws from it."""
    telegram = store.capture("info", 10, 5, "shared", received_at="2026-08-09T10:00:00+00:00")
    allocated = store.allocate_local_id("info")
    http = store.capture(
        "info",
        0,
        allocated,
        "posted",
        received_at="2026-08-09T10:00:00+00:00",
        reserved_local_id=allocated,
    )

    assert telegram.path != http.path
    assert {telegram.local_id, http.local_id} == {1, 2}


def test_each_route_runs_only_its_own_steps(tmp_path):
    store = make_store(tmp_path)
    info_step, job_step = RecordingStep("info-only"), RecordingStep("job-only")
    pipelines = {
        "info": ProcessingPipeline([info_step]),
        "job": ProcessingPipeline([job_step]),
        "clip": ProcessingPipeline([]),
        "lang": ProcessingPipeline([]),
    }
    worker = ProcessingWorker(store, pipelines)
    coordinator = ProcessingCoordinator(store, pipelines, worker)

    for message_id, route in enumerate(("job", "info", "job"), 1):
        coordinator.submit(store.capture(route, 10, message_id, route))
    while (job := store.claim_next_received()) is not None:
        worker._process(job)

    assert [route for route, _ in info_step.seen] == ["info"]
    assert [route for route, _ in job_step.seen] == ["job", "job"]


def test_an_unconfigured_route_delivers_the_item_rather_than_failing_it(tmp_path, caplog):
    """A configuration mistake must never withhold something that was captured."""
    store = make_store(tmp_path)
    pipelines = {"info": ProcessingPipeline([RecordingStep("info-only")])}
    worker = ProcessingWorker(store, pipelines)
    store.capture("clip", 10, 1, "a reel", received_at="2026-08-09T10:00:00+00:00")

    job = store.claim_next_received()
    worker._process(job)

    assert store.get_item("clip", 10, 1)["status"] == "ready"
    assert (store.inbox_dir / "clip" / job.path.name / "capture" / "message.md").is_file()


def test_a_route_with_no_steps_is_promoted_straight_into_its_inbox(tmp_path):
    store = make_store(tmp_path)
    pipelines = {route: ProcessingPipeline([]) for route in store.routes}
    worker = ProcessingWorker(store, pipelines)
    coordinator = ProcessingCoordinator(store, pipelines, worker)

    coordinator.submit(store.capture("lang", 10, 1, "sobremesa"))

    assert store.get_item("lang", 10, 1)["status"] == "ready"


# --- the hashtag correction path -------------------------------------------


def hashtag_payload(text: str) -> dict:
    """A payload whose `#word` runs are marked up as Telegram marks them up."""
    entities = []
    for token in text.split():
        if token.startswith("#"):
            offset = len(text[: text.index(token)].encode("utf-16-le")) // 2
            entities.append(
                {
                    "type": "hashtag",
                    "offset": offset,
                    "length": len(token.encode("utf-16-le")) // 2,
                }
            )
    return {"message_id": 1, "date": 100, "text": text, "entities": entities}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a posting #job", "job"),
        ("#lang sobremesa", "lang"),
        ("A REEL #CLIP", "clip"),
        ("just a note", None),
        ("#job and #clip at once", None),
        ("#todo is not a route", None),
    ],
)
def test_a_route_hashtag_is_read_from_the_entities(text, expected):
    assert requested_route([hashtag_payload(text)], ROUTES) == expected


def test_a_hashtag_names_a_route_only_where_that_route_is_configured():
    payload = hashtag_payload("#lang sobremesa")
    assert requested_route([payload], ("info", "job")) is None
    assert requested_route([hashtag_payload("#recipes pasta")], ("info", "recipes")) == "recipes"


def test_a_hashtag_inside_a_url_is_not_a_routing_instruction():
    """Telegram does not mark a fragment as a hashtag, and neither do we."""
    payload = {
        "message_id": 1,
        "date": 100,
        "text": "https://example.com/a#clip",
        "entities": [{"type": "url", "offset": 0, "length": 26}],
    }
    assert requested_route([payload], ROUTES) is None


def test_a_hashtag_moves_the_item_and_renumbers_it_in_the_destination(store):
    """The correction path: shared to the wrong bot, fixed by editing the message."""
    store.capture("job", 10, 99, "an unrelated posting", received_at="2026-08-09T10:00:00+00:00")
    original = store.capture("info", 10, 1, "a posting", received_at="2026-08-09T10:00:00+00:00")
    assert store.promote_if_current(original)

    moved = store.capture(
        "info",
        10,
        1,
        "a posting #job",
        route="job",
        edited_at="2026-08-09T10:05:00+00:00",
        received_at="2026-08-09T10:00:00+00:00",
    )

    assert moved.route == "job"
    assert moved.origin_route == "info"
    assert moved.revision == 2
    # Renumbered, because the destination route's number 1 is already taken.
    assert moved.local_id == 2
    assert not (store.inbox_dir / "info" / original.path.name).exists()
    assert not (store.staging_dir / "info" / original.path.name).exists()
    assert (moved.path / "capture" / "message.md").read_text() == "a posting #job"


def test_an_edit_after_a_move_still_finds_the_item_on_the_original_bot(store):
    """The Telegram message never leaves the chat it was sent to."""
    store.capture("info", 10, 1, "a posting", received_at="2026-08-09T10:00:00+00:00")
    store.capture(
        "info", 10, 1, "a posting #job", route="job", edited_at="2026-08-09T10:05:00+00:00"
    )

    # A later edit arrives on the info bot, as every edit to this message will.
    again = store.capture(
        "info", 10, 1, "a better posting #job", edited_at="2026-08-09T10:09:00+00:00"
    )

    assert again.route == "job"
    assert again.revision == 3
    assert store.get_item("info", 10, 1)["route"] == "job"
    assert len(list((store.staging_dir / "job").iterdir())) == 1


def test_an_interrupted_move_is_repaired_from_the_directory_on_disk(tmp_path):
    """metadata.json is written before the rename, so the directory is authority."""
    store = make_store(tmp_path)
    store.capture("info", 10, 1, "a posting", received_at="2026-08-09T10:00:00+00:00")

    # Simulate a crash between the rename and the state write: move the directory
    # as capture() would, leaving the row still naming the old route.
    staged = store.staging_for("info", "2026-08-09T10:00:00+00:00", 1)
    metadata = json.loads((staged / "metadata.json").read_text())
    metadata.update({"route": "job", "local_id": 7})
    (staged / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    staged.rename(store.staging_for("job", "2026-08-09T10:00:00+00:00", 7))

    recovered = make_store(tmp_path)
    item = recovered.get_item("info", 10, 1)

    assert (item["route"], item["local_id"], item["status"]) == ("job", 7, "received")


def test_a_database_from_before_routes_is_refused_rather_than_migrated(tmp_path):
    import sqlite3

    (tmp_path / "staging").mkdir()
    connection = sqlite3.connect(tmp_path / "info-triage.sqlite3")
    connection.execute(
        "CREATE TABLE items (chat_id INTEGER, message_id INTEGER, status TEXT,"
        " PRIMARY KEY (chat_id, message_id))"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="predates routes"):
        make_store(tmp_path)
