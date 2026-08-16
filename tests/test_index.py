import pytest

from info_triage.extraction import ExtractionRecord
from info_triage.index import build_segments, detect_intent, render_index
from info_triage.models import LinkTableEntry, ProcessingIssue, ProcessingProblem

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


def index(
    payloads,
    message,
    links=(),
    metadata=METADATA,
    threshold=8,
    extractions=(),
    problems=(),
):
    return render_index(
        "2026-08-11_100",
        metadata,
        payloads,
        message,
        list(links),
        list(extractions),
        list(problems),
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


def test_the_links_list_prefers_the_resolved_title_then_the_authors_label():
    links = [
        link(1, status="resolved", title="Throughput vs Latency — AWS", label="Latency"),
        link(2, status="unresolved", reason="title-not-found", label="CAP Theorem"),
        link(3, status="skipped", reason="resolve-budget-exhausted"),
        link(4, status="duplicate", duplicate_of=1),
    ]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert "1. [Throughput vs Latency — AWS](https://example.com/1) — document · resolved" in rendered
    assert (
        "2. [CAP Theorem](https://example.com/2) — document · unresolved (title-not-found)"
        in rendered
    )
    assert "3. [https://example.com/3](https://example.com/3) — document ·" in rendered
    assert (
        "4. [https://example.com/4](https://example.com/4) — document · duplicate of 1" in rendered
    )


def test_links_are_a_list_because_a_table_cannot_be_made_narrow():
    """A table is as wide as its widest row, and these rows carry page titles.

    In a half-width editor window the columns cannot fit; a reader that hides
    link markup makes it worse by pinning each separator to its source column.
    A list has no columns to lose.
    """
    links = [link(1, status="resolved", title="A title")]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)

    assert "## Links" in rendered
    assert not any(line.startswith("|") for line in rendered.splitlines())


def test_a_link_label_cannot_reopen_markdown():
    """A pipe needs no escaping outside a table, but the rest still does."""
    links = [link(1, status="resolved", title="A | B *starred* [x]")]
    rendered = index([payload(1, 100, text="…")], body(("text", "x")), links)
    assert "[A | B \\*starred\\* \\[x\\]](https://example.com/1)" in rendered


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
    assert (
        "1. [extracted/01-document-x/content.md](extracted/01-document-x/content.md)"
        " — A Paper · A. Author · 2024-10-07" in rendered
    )
    assert "complete · 1,234 words" in rendered
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
    assert field(rendered, "lead") == "excerpt"


def test_short_form_media_leads_with_what_only_the_pipeline_recovered():
    """A reel's caption is marketing; the payload is in the audio and on screen.

    Real regression: item 2026-08-14_151 spent its whole Lead on a book plug
    while the spoken audio — what its own `intent` referred to — never appeared.
    """
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://www.instagram.com/reel/A/")),
        [link(1, handler="instagram", priority=2)],
        extractions=[
            extraction(
                handler="instagram",
                kind="post",
                short_form=True,
                sections=[
                    ("Caption", "Buy my book on Mercado Libre."),
                    ("On-screen text", "ALTER EGO"),
                    ("Spoken audio", "Everyone has two versions of themselves."),
                ],
            )
        ],
    )

    lead = rendered.split("## Lead\n\n")[1].split("\n\n## ")[0]
    assert lead.index("On-screen text") < lead.index("Spoken audio") < lead.index("Caption")
    assert "> **Spoken audio**" in lead
    assert "Everyone has two versions of themselves." in lead
    # Nothing was cut, so opening content.md is optional rather than a guess.
    assert field(rendered, "lead") == "full"


def test_the_budget_is_shared_fairly_rather_than_first_come():
    """A Short's burned-in subtitles make its OCR nearly as long as its transcript.

    Spending the budget in stream order left item 2026-08-14_150's description —
    the only stream carrying the nearby places — with the scraps.
    """
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://www.youtube.com/shorts/A/")),
        [link(1, handler="youtube", priority=2)],
        extractions=[
            extraction(
                handler="youtube",
                kind="video",
                short_form=True,
                sections=[
                    ("Description", "unique " * 200 + "Jiuhuang Mountain"),
                    ("Transcript (youtube_automatic)", "spoken " * 500),
                    ("On-screen text", "onscreen " * 490),
                ],
            )
        ],
    )

    lead = rendered.split("## Lead\n\n")[1].split("\n\n## ")[0]
    # The short stream is quoted whole; the two long ones divide what is left.
    assert "Jiuhuang Mountain" in lead
    assert "**On-screen text**" in lead
    assert "**Transcript (youtube_automatic)**" in lead


def test_a_long_stream_cannot_starve_the_others_out_of_the_lead():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://www.instagram.com/reel/A/")),
        [link(1, handler="instagram", priority=2)],
        extractions=[
            extraction(
                handler="instagram",
                kind="post",
                short_form=True,
                sections=[("On-screen text", "screen " * 2000), ("Caption", "caption " * 40)],
            )
        ],
    )

    lead = rendered.split("## Lead\n\n")[1].split("\n\n## ")[0]
    assert "**Caption**" in lead
    assert "caption" in lead
    assert field(rendered, "lead") == "excerpt"


def test_a_forwarded_note_leads_a_short_form_lead_without_replacing_it():
    rendered = index(
        [payload(1, 100, text="Worth watching", forward_origin={"type": "user"})],
        body(("text", "Worth watching https://www.instagram.com/reel/A/")),
        [link(1, handler="instagram", priority=2)],
        extractions=[
            extraction(
                handler="instagram",
                kind="post",
                short_form=True,
                sections=[("Spoken audio", "The video says this.")],
            )
        ],
    )

    lead = rendered.split("## Lead\n\n")[1].split("\n\n## ")[0]
    assert lead.index("Worth watching") < lead.index("The video says this.")


def test_a_papers_abstract_still_wins_over_its_sections():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://arxiv.org/abs/1")),
        [link(1, handler="research", priority=1)],
        extractions=[
            extraction(
                handler="research",
                kind="paper",
                abstract="We establish model collapse.",
                sections=[("Abstract", "ignore"), ("Full paper", "ignore")],
            )
        ],
    )

    assert "## Lead\n\n> We establish model collapse." in rendered
    assert field(rendered, "lead") == "abstract"


def test_sources_link_a_kept_pdf_so_the_readable_copy_is_findable():
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://arxiv.org/abs/1")),
        [link(1, handler="research", priority=1)],
        extractions=[extraction(handler="research", kind="paper", pdf="raw/paper.pdf")],
    )

    assert "· [pdf](extracted/01-document-x/raw/paper.pdf)" in rendered


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


# Nested extraction — the item is promoted to what it turned out to be.


def test_a_post_whose_author_comment_carries_a_paper_is_indexed_as_the_paper():
    """The LinkedIn wrapper is transport; the paper is the artifact `vet` judges."""
    links = [
        link(1, handler="linkedin", priority=2, canonical="https://linkedin.com/posts/activity-7"),
        link(
            2,
            handler="research",
            priority=1,
            canonical="https://arxiv.org/abs/2606.19857",
            status="harvested",
            origin="harvest",
            via="author comment",
            title="BabelTele",
        ),
    ]
    extractions = [
        extraction(
            1,
            handler="linkedin",
            directory="extracted/01-linkedin-7",
            kind="post",
            title="LLMs don't need readable text",
            word_count=280,
            excerpt="The post's own marketing prose.",
        ),
        extraction(
            2,
            handler="research",
            directory="extracted/02-research-arxiv-2606.19857",
            kind="paper",
            title="BabelTele",
            word_count=9400,
            abstract="We study byte-level tokenizers.",
            via="01 · author comment",
        ),
    ]

    rendered = index(
        [payload(1, 100, text="…")], body(("text", "…")), links, extractions=extractions
    )

    assert field(rendered, "kind") == "paper"
    assert field(rendered, "title") == '"BabelTele"'
    assert field(rendered, "canonical_url") == "https://arxiv.org/abs/2606.19857"
    assert field(rendered, "sources") == "2"
    # The abstract leads, not the post that pointed at it.
    assert "> We study byte-level tokenizers." in rendered
    assert "marketing prose" not in rendered
    # Provenance for a source the user never sent stays visible.
    assert "via 01 · author comment" in rendered


def test_harvested_links_do_not_turn_a_small_item_into_a_link_list():
    """The budget was decided on what arrived; the reported kind must agree."""
    links = [link(n, handler="youtube" if n == 1 else "document") for n in range(1, 6)]
    links.extend(
        link(n, status="harvested", origin="harvest", via="description") for n in range(6, 9)
    )

    rendered = index([payload(1, 100, text="…")], body(("text", "…")), links)

    assert field(rendered, "kind") != "linklist"
    assert field(rendered, "link_count") is None
    # Every row is still listed, harvested or not.
    assert rendered.count("— document · ") == 7
    # The same eight rows, all of them the user's, are a link list.
    captured = [link(n) for n in range(1, 9)]
    assert field(index([payload(1, 100, text="…")], body(("text", "…")), captured), "kind") == (
        "linklist"
    )


def test_preprocessing_problems_are_declared_in_the_item():
    """A thin item must say what failed, and still be an item."""
    problems = [
        ProcessingProblem(
            "content-extraction",
            "partial",
            ProcessingIssue("wall-clock-exceeded", "Extraction budget of 300s was spent"),
        ),
        ProcessingProblem(
            "url-resolution",
            "failed",
            ProcessingIssue(
                "exception-connection-error",
                "connection reset",
                target="https://example.com/1",
                error_type="ConnectionError",
            ),
        ),
    ]

    rendered = index(
        [payload(1, 100, text="look https://example.com/1")],
        body(("text", "look https://example.com/1")),
        [link(1)],
        problems=problems,
    )

    assert field(rendered, "problems") == "2"
    assert "## Problems" in rendered
    assert "- `content-extraction` partial — `wall-clock-exceeded`" in rendered
    assert (
        "- `url-resolution` failed — `exception-connection-error` — "
        "https://example.com/1 — connection reset"
    ) in rendered
    # The capture itself is still the point of the file.
    assert "## Captured" in rendered
    assert "look https://example.com/1" in rendered


def test_an_item_without_problems_says_nothing_about_them():
    rendered = index([payload(1, 100, text="hello")], body(("text", "hello")))

    assert field(rendered, "problems") is None
    assert "## Problems" not in rendered


def test_a_titled_item_carries_no_headline():
    """`headline` is a stand-in, so `title or headline` never labels an item twice."""
    rendered = index(
        [payload(1, 100, text="…")],
        body(("text", "https://arxiv.org/abs/1")),
        [link(1, handler="research", priority=1)],
        extractions=[extraction(kind="paper", title="Strong Model Collapse")],
    )

    assert field(rendered, "title") == '"Strong Model Collapse"'
    assert field(rendered, "headline") is None


def test_an_untitled_post_is_named_by_its_caption():
    """An Instagram post has no title, and a bare date and kind name nothing.

    The caption leads the stream order because it is the one a person wrote on
    purpose — unlike the OCR of burned-in subtitles sitting right next to it.
    """
    rendered = index(
        [payload(1, 100, text="https://instagram.com/reel/x")],
        body(("text", "https://instagram.com/reel/x")),
        [link(1, handler="instagram")],
        extractions=[
            extraction(
                handler="instagram",
                kind="post",
                title=None,
                short_form=True,
                sections=[
                    ("On-screen text", "when they together in this place\nthis is elephant"),
                    (
                        "Caption",
                        "Ever noticed the colored tags on the ancient trees in the "
                        "Forbidden City? Here is what they actually mean.\n\n"
                        "While walking through the Imperial Garden…",
                    ),
                ],
            )
        ],
    )

    assert field(rendered, "headline") == (
        '"Ever noticed the colored tags on the ancient trees in the Forbidden City?"'
    )


def test_a_headline_skips_a_caption_that_opens_with_its_hashtags():
    rendered = index(
        [payload(1, 100, text="https://instagram.com/reel/x")],
        body(("text", "https://instagram.com/reel/x")),
        [link(1, handler="instagram")],
        extractions=[
            extraction(
                handler="instagram",
                kind="post",
                title=None,
                short_form=True,
                sections=[("Caption", "#travel #china @someone\nHidden next to Chongqing.")],
            )
        ],
    )

    assert field(rendered, "headline") == '"Hidden next to Chongqing."'


def test_a_long_headline_is_cut_on_a_word_boundary():
    caption = "Nine ways to " + "word " * 40
    rendered = index(
        [payload(1, 100, text="https://instagram.com/reel/x")],
        body(("text", "https://instagram.com/reel/x")),
        [link(1, handler="instagram")],
        extractions=[
            extraction(
                handler="instagram", kind="post", title=None, sections=[("Caption", caption)]
            )
        ],
    )

    headline = field(rendered, "headline").strip('"')
    assert len(headline) <= 100
    assert headline.endswith("…")
    assert not headline.endswith(" …")


def test_an_item_with_nothing_extracted_is_named_by_its_address():
    """The degenerate case: a story URL that canonicalized down to a profile.

    Nothing was extracted and the resolver's title is a display name, so the
    address is the only thing left that says which item this is.
    """
    rendered = index(
        [payload(1, 100, text="https://www.instagram.com/marlidiuret/")],
        body(("text", "https://www.instagram.com/marlidiuret/")),
        [link(1, canonical="https://www.instagram.com/marlidiuret/", status="unresolved")],
    )

    assert field(rendered, "headline") == '"instagram.com/marlidiuret"'


def test_an_untitled_item_falls_back_to_the_users_own_note():
    rendered = index(
        [payload(1, 100, text="translate this and add to my reflection plan")],
        body(("text", "translate this and add to my reflection plan")),
    )

    assert field(rendered, "headline") == '"translate this and add to my reflection plan"'
