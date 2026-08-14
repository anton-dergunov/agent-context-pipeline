import pytest

from info_triage.extraction import ExtractionRecord
from info_triage.index import build_segments, detect_intent, render_index
from info_triage.models import LinkTableEntry

METADATA = {"received_at": "2026-08-11T15:33:28+01:00"}


def payload(message_id, date, **values):
    return {"message_id": message_id, "date": date, **values}


def body(*segments):
    return "\n\n".join(
        f"## Segment {number} — {kind}\n\n{text}" if text else f"## Segment {number} — {kind}"
        for number, (kind, text) in enumerate(segments, 1)
    )


def link(n=1, **values):
    defaults = {
        "raw": f"https://example.com/{n}",
        "canonical": f"https://example.com/{n}",
        "handler": "document",
        "priority": 4,
    }
    return LinkTableEntry(n=n, **{**defaults, **values})


def index(payloads, message, links=(), metadata=METADATA, threshold=8, extractions=()):
    return render_index(
        "2026-08-11_100",
        metadata,
        payloads,
        message,
        list(links),
        list(extractions),
        linklist_threshold=threshold,
    )


def extraction(link_n=1, **values):
    defaults = {
        "handler": "document",
        "identity": "x",
        "directory": f"extracted/{link_n:02d}-document-x",
        "status": "complete",
        "kind": "article",
        "word_count": 1234,
    }
    return ExtractionRecord(link_n=link_n, **{**defaults, **values})


def field(rendered, name):
    lines = rendered.splitlines()
    assert lines[0] == "---"
    for line in lines[1:]:
        if line == "---":
            return None
        if line.startswith(f"{name}: "):
            return line.removeprefix(f"{name}: ")
    raise AssertionError("frontmatter is not closed")


def segments_of(payloads, message):
    built = build_segments(payloads, message)
    assert built is not None
    return built


# Intent detection — the three heuristics, and the honest empty answer.


def test_short_text_after_the_last_url_in_a_segment_is_the_note():
    message = body(("text", "https://www.instagram.com/reel/DbW0FoHI1OO/ Try loops"))
    assert detect_intent(segments_of([payload(1, 100, text="…")], message)) == "Try loops"


def test_short_unforwarded_segment_beside_a_forwarded_one_is_the_note():
    message = body(
        ("forwarded text", "A long forwarded post about something else entirely."),
        ("text", "I mean, I'm very curious to try this idea"),
    )
    payloads = [
        payload(1, 100, text="…", forward_origin={"type": "hidden_user"}),
        payload(2, 101, text="…"),
    ]
    assert (
        detect_intent(segments_of(payloads, message)) == "I mean, I'm very curious to try this idea"
    )


def test_a_forwarded_segment_is_never_the_note():
    message = body(
        ("forwarded text", "https://example.com/a Short remark"),
        ("forwarded text", "Another forwarded line"),
    )
    payloads = [
        payload(1, 100, text="…", forward_origin={"type": "channel"}),
        payload(2, 101, text="…", forward_from_chat={"title": "Channel"}),
    ]
    assert detect_intent(segments_of(payloads, message)) is None


def test_long_trailing_text_is_the_material_not_a_note():
    message = body(("text", "https://example.com/a " + "word " * 60))
    assert detect_intent(segments_of([payload(1, 100, text="…")], message)) is None


def test_two_candidates_disagreeing_leave_the_intent_empty():
    message = body(
        ("text", "https://example.com/a Try this"),
        ("text", "https://example.com/b And this"),
    )
    payloads = [payload(1, 100, text="…"), payload(2, 101, text="…")]
    assert detect_intent(segments_of(payloads, message)) is None


def test_a_lone_short_segment_with_no_neighbour_is_not_a_note():
    message = body(("text", "Just a thought"))
    assert detect_intent(segments_of([payload(1, 100, text="…")], message)) is None


def test_a_body_that_does_not_match_its_payloads_yields_no_segments():
    assert build_segments([payload(1, 100, text="…")], "no headings here") is None
    assert build_segments([], body(("text", "https://example.com/a Note"))) is None


# Frontmatter.


def test_a_voice_note_renders_the_case_one_shape():
    message = body(("voice", "Интересно, как он справится"))
    rendered = index([payload(1, 100, voice={"file_id": "x"})], message)
    assert field(rendered, "origin") == "voice"
    assert field(rendered, "intent") == "null"
    assert field(rendered, "kind") == "note"
    assert field(rendered, "extraction") == "none"
    assert (
        "> Интересно, как он справится\n>\n> — transcribed from a Telegram voice message"
        in rendered
    )
    assert "## Links" not in rendered


def test_captured_at_is_utc_and_id_comes_from_the_directory():
    rendered = index([payload(1, 100, text="…")], body(("text", "hello")))
    assert field(rendered, "id") == "2026-08-11_100"
    assert field(rendered, "captured_at") == "2026-08-11T14:33:28Z"


def test_an_unreadable_received_at_omits_captured_at_rather_than_guessing():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "hello")),
        metadata={"received_at": "2026-08-11 15:33:28"},
    )
    assert field(rendered, "captured_at") is None
    assert field(rendered, "id") == "2026-08-11_100"


@pytest.mark.parametrize(
    ("provenance", "expected"),
    [
        (
            {"forward_origin": {"type": "channel", "chat": {"username": "ai_big_data"}}},
            "@ai_big_data (forwarded channel)",
        ),
        (
            {"forward_origin": {"type": "channel", "chat": {"title": "Machinelearning"}}},
            "Machinelearning (forwarded channel)",
        ),
        (
            {"forward_origin": {"type": "hidden_user", "sender_user_name": "Some One"}},
            "Some One (forwarded user)",
        ),
        (
            {"forward_from_chat": {"type": "channel", "title": "Old Channel"}},
            "Old Channel (forwarded channel)",
        ),
        ({"forward_sender_name": "Hidden Sender"}, "Hidden Sender (forwarded user)"),
    ],
)
def test_via_carries_the_forwarding_provenance(provenance, expected):
    message = body(("forwarded text", "post"))
    rendered = index([payload(1, 100, text="…", **provenance)], message)
    assert field(rendered, "via") == f'"{expected}"'
    assert field(rendered, "origin") == "telegram"


def test_an_unforwarded_item_has_no_via():
    rendered = index([payload(1, 100, text="…")], body(("text", "hello")))
    assert field(rendered, "via") is None


def test_origin_comes_from_the_highest_priority_link():
    links = [
        link(1, canonical="https://example.com/a", handler="document", priority=4),
        link(2, canonical="https://youtu.be/abc", handler="youtube", priority=2),
    ]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert field(rendered, "origin") == "youtube"
    assert field(rendered, "canonical_url") == "https://youtu.be/abc"


def test_a_research_link_reports_a_web_origin():
    links = [link(1, canonical="https://arxiv.org/abs/1", handler="research", priority=1)]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert field(rendered, "origin") == "web"


def test_a_link_pile_becomes_a_linklist_and_reports_its_count():
    links = [link(n) for n in range(1, 9)]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert field(rendered, "kind") == "linklist"
    assert field(rendered, "link_count") == "8"


def test_a_handful_of_links_leaves_kind_and_link_count_to_extraction():
    links = [link(n) for n in range(1, 4)]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert field(rendered, "kind") is None
    assert field(rendered, "link_count") is None


def test_duplicates_do_not_count_towards_the_link_list_threshold():
    links = [link(n) for n in range(1, 8)]
    links.extend(
        link(n, status="duplicate", duplicate_of=1, canonical="https://example.com/1")
        for n in (8, 9)
    )
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links, threshold=8)
    assert field(rendered, "kind") is None
    assert field(rendered, "link_count") == "7"


def test_an_unresolved_link_makes_the_extraction_partial_with_its_reason():
    links = [
        link(1, status="resolved", title="Fine"),
        link(2, status="unresolved", reason="http-error"),
    ]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert field(rendered, "extraction") == "partial"
    assert field(rendered, "reason") == "http-error"


def test_a_skipped_link_is_also_a_partial_extraction():
    links = [link(1, status="skipped", reason="resolve-budget-exhausted")]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert field(rendered, "extraction") == "partial"
    assert field(rendered, "reason") == "resolve-budget-exhausted"


def test_prose_fields_are_quoted_and_kept_on_one_line():
    message = body(
        ("forwarded text", "material"),
        ("text", 'Read: "notes" \\ and\nreflect'),
    )
    payloads = [
        payload(1, 100, text="…", forward_origin={"type": "channel"}),
        payload(2, 101, text="…"),
    ]
    rendered = index(payloads, message)
    assert field(rendered, "intent") == '"Read: \\"notes\\" \\\\ and reflect"'


# Body sections.


def test_captured_holds_every_unforwarded_segment_when_no_intent_is_detected():
    message = body(("text", "First thought here"), ("text", "Second thought here"))
    payloads = [payload(1, 100, text="…"), payload(2, 101, text="…")]
    rendered = index(payloads, message)
    assert field(rendered, "intent") == "null"
    assert "> First thought here\n>\n> Second thought here" in rendered


def test_a_forward_without_a_note_says_so():
    message = body(("forwarded text", "somebody else's post"))
    rendered = index([payload(1, 100, text="…", forward_origin={"type": "channel"})], message)
    assert "> [forwarded — no note of your own]" in rendered
    # Captured stays the user's own words; the forwarded material is not his.
    assert "## Captured\n\n> [forwarded" in rendered


def test_forwarded_material_is_the_lead_so_its_links_have_a_context():
    """Without this the post body lives only in capture/ and its links look invented."""
    message = body(("forwarded text", "Новая статья. Разбор на Хабре."))
    rendered = index([payload(1, 100, text="…", forward_origin={"type": "channel"})], message)
    assert "## Lead\n\n> Новая статья. Разбор на Хабре." in rendered


def test_a_forwarded_lead_is_truncated_at_the_configured_length():
    forwarded = " ".join(f"word{number}" for number in range(200))
    message = body(("forwarded text", forwarded))
    rendered = index([payload(1, 100, text="…", forward_origin={"type": "channel"})], message)
    assert "word9 " in rendered
    assert "word150" not in rendered
    assert "…" in rendered


def test_an_unpairable_body_keeps_everything_in_captured():
    rendered = index([], "loose text with no segment headings")
    assert "> loose text with no segment headings" in rendered
    assert field(rendered, "intent") == "null"


def test_the_links_table_prefers_the_resolved_title_then_the_authors_label():
    links = [
        link(1, status="resolved", title="Throughput vs Latency — AWS", label="Latency"),
        link(2, status="unresolved", reason="title-not-found", label="CAP Theorem"),
        link(3, status="skipped", reason="resolve-budget-exhausted"),
        link(4, status="duplicate", duplicate_of=1),
    ]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert "| 1 | [Throughput vs Latency — AWS](https://example.com/1) | document |" in rendered
    assert (
        "| 2 | [CAP Theorem](https://example.com/2) | document | unresolved (title-not-found) |"
        in rendered
    )
    assert "| 3 | [https://example.com/3](https://example.com/3) |" in rendered
    assert (
        "| 4 | [https://example.com/4](https://example.com/4) | document | duplicate of 1 |"
        in rendered
    )


def test_table_cells_cannot_break_the_table_or_reopen_markdown():
    links = [link(1, status="resolved", title="A | B *starred* [x]")]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert "[A \\| B \\*starred\\* \\[x\\]](https://example.com/1)" in rendered


def test_a_body_that_cannot_be_paired_still_keeps_segment_headings_out():
    message = body(("text", "first"), ("text", "second"))
    rendered = index([payload(1, 100, text="…")], message)
    assert "## Segment" not in rendered
    assert "> first\n>\n> second" in rendered


def test_sources_print_the_word_count_that_makes_drill_down_a_choice():
    """Without the cost, choosing between an abstract and a 12k-word body is blind."""
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://example.com/1")),
        [link(1)],
        extractions=[extraction(title="A Paper", authors=["A. Author"], published="2024-10-07")],
    )

    assert "## Sources" in rendered
    assert "1. `extracted/01-document-x/` — A Paper · A. Author · 2024-10-07" in rendered
    assert "complete · `content.md` 1,234 words" in rendered
    assert field(rendered, "sources") == "1"


def test_a_papers_lead_is_its_abstract_not_its_opening_prose():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://arxiv.org/abs/1")),
        [link(1, handler="research", priority=1)],
        extractions=[
            extraction(kind="paper", abstract="We establish model collapse.", excerpt="Ignore me.")
        ],
    )

    assert "## Lead\n\n> We establish model collapse." in rendered
    assert "Ignore me" not in rendered


def test_a_non_paper_lead_is_the_opening_of_its_body():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://example.com/1")),
        [link(1)],
        extractions=[extraction(excerpt="The article opens like this.")],
    )

    assert "## Lead\n\n> The article opens like this." in rendered


def test_frontmatter_carries_what_extraction_learned():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://arxiv.org/abs/1")),
        [link(1, handler="research", priority=1)],
        extractions=[
            extraction(
                kind="paper",
                title="Strong Model Collapse",
                authors=["Elvis Dohmatob", "Julia Kempe"],
                published="2024-10-07",
                venue="arXiv",
                doi="10.48550/arXiv.2410.04840",
            )
        ],
    )

    assert field(rendered, "kind") == "paper"
    assert field(rendered, "title") == '"Strong Model Collapse"'
    assert field(rendered, "authors") == '["Elvis Dohmatob", "Julia Kempe"]'
    assert field(rendered, "venue") == '"arXiv"'
    assert field(rendered, "extraction") == "ok"


def test_a_blocked_source_says_so_rather_than_reading_as_a_gap():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://medium.com/x")),
        [link(1, handler="medium")],
        extractions=[extraction(status="blocked", reason="access-blocked", kind=None)],
    )

    assert field(rendered, "extraction") == "failed"
    assert field(rendered, "reason") == "access-blocked"
    assert "blocked (`access-blocked`)" in rendered


def test_a_member_preview_is_partial_because_it_cannot_be_judged():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://medium.com/x")),
        [link(1, handler="medium")],
        extractions=[extraction(status="partial", reason="medium-member-preview", word_count=264)],
    )

    assert field(rendered, "extraction") == "partial"
    assert field(rendered, "reason") == "medium-member-preview"


def test_an_excluded_link_leaves_links_json_but_never_the_index():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://example.com/1")),
        [link(1), link(2, status="excluded", reason="telegram-channel")],
    )

    assert "example.com/1" in rendered
    assert "example.com/2" not in rendered
