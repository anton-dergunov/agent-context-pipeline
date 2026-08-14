import pytest

from info_triage.links import (
    build_link_table,
    canonicalize_url,
    exclusion_reason,
    is_deprioritized,
    link_priority,
    unwrap_url,
)


def payload(message_id, date, **values):
    return {"message_id": message_id, "date": date, **values}


def text_link(offset, length, url):
    return {"type": "text_link", "offset": offset, "length": length, "url": url}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://example.com/a?utm_source=x&utm_campaign=y&id=7",
            "https://example.com/a?id=7",
        ),
        ("https://instagram.com/reel/abc/?igsh=MTk=", "https://instagram.com/reel/abc/"),
        ("https://youtu.be/abc?si=token&t=30", "https://youtu.be/abc?t=30"),
        ("https://example.com/a?is=1&fbclid=2&rcm=3", "https://example.com/a"),
        ("https://feverup.com/e/1?cp_landing_page=x&cp_landing=y", "https://feverup.com/e/1"),
        ("HTTPS://Example.COM:443/Path", "https://example.com/Path"),
        ("https://example.com:8443/p", "https://example.com:8443/p"),
        ("https://example.com/p#section", "https://example.com/p#section"),
        ("mailto:someone@example.com", "mailto:someone@example.com"),
    ],
)
def test_canonicalization(url, expected):
    assert canonicalize_url(url) == expected


def test_embedded_destinations_are_unwrapped():
    wrapped = "https://www.linkedin.com/safety/go?url=https%3A%2F%2Farxiv.org%2Fabs%2F2305.03509"

    assert unwrap_url(wrapped) == "https://arxiv.org/abs/2305.03509"


def test_hidden_entity_hrefs_reach_the_table():
    payloads = [
        payload(
            1,
            100,
            text="Diffusion explainer\nGithub",
            entities=[
                text_link(0, 19, "https://poloclub.github.io/diffusion-explainer/"),
                text_link(20, 6, "https://github.com/poloclub/diffusion-explainer"),
            ],
        )
    ]

    table = build_link_table(payloads, "## Segment 1 — text\n\nDiffusion explainer\nGithub")

    assert [(entry.n, entry.canonical, entry.label) for entry in table] == [
        (1, "https://poloclub.github.io/diffusion-explainer/", "Diffusion explainer"),
        (2, "https://github.com/poloclub/diffusion-explainer", "Github"),
    ]
    assert {entry.origin for entry in table} == {"entity"}
    assert {entry.from_segment for entry in table} == {1}
    assert {entry.status for entry in table} == {"discovered"}


def test_entity_and_body_occurrences_of_one_target_share_a_row():
    payloads = [
        payload(
            1,
            100,
            text="paper",
            entities=[text_link(0, 5, "https://arxiv.org/abs/2305.03509?utm_source=tg")],
        )
    ]
    body = "## Segment 1 — text\n\nhttps://arxiv.org/abs/2305.03509"

    table = build_link_table(payloads, body)

    assert len(table) == 1
    assert table[0].canonical == "https://arxiv.org/abs/2305.03509"
    assert table[0].handler == "research"
    assert table[0].origin == "entity"


def test_body_links_are_attributed_to_their_segment():
    body = (
        "## Segment 1 — text\n\nhttps://example.com/one\n\n"
        "## Segment 2 — forwarded text\n\n[Two](https://example.com/two)"
    )

    table = build_link_table([], body)

    assert [(entry.n, entry.from_segment, entry.label, entry.origin) for entry in table] == [
        (1, 1, None, "body"),
        (2, 2, "Two", "body"),
    ]


def test_url_entities_without_a_scheme_are_usable():
    payloads = [
        payload(
            1,
            100,
            text="see example.com/post",
            entities=[{"type": "url", "offset": 4, "length": 16}],
        )
    ]

    table = build_link_table(payloads, "")

    assert table[0].canonical == "https://example.com/post"


def test_a_research_link_outranks_everything_else():
    table = build_link_table(
        [],
        (
            "## Segment 1 — text\n\nhttps://arxiv.org/abs/2305.03509\n"
            "https://github.com/owner/repo\n"
            "https://example.com/article\n"
            "https://t.me/some_channel\n"
        ),
    )

    assert [(entry.handler, entry.priority) for entry in table] == [
        ("research", 1),
        ("document", 3),
        ("document", 4),
        ("document", 9),
    ]


def test_the_only_link_in_an_item_earns_the_budget():
    table = build_link_table([], "## Segment 1 — text\n\nhttps://example.com/article")

    assert table[0].priority == 2


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/someone",
        "https://t.me/ai_machinelearning_big_data",
        "https://www.linkedin.com/feed/",
        "https://twitter.com/hashtag/GenAI",
        "https://pbs.twimg.com/media/abc.jpg",
        "https://amzn.to/3xyz",
    ],
)
def test_links_that_never_deserve_extraction(url):
    assert is_deprioritized(url)


def test_specific_artifacts_on_social_hosts_are_not_deprioritized():
    assert not is_deprioritized("https://www.instagram.com/reel/abc/")
    assert link_priority("instagram", "https://www.instagram.com/reel/abc/", sole_link=False) == 5


@pytest.mark.parametrize(
    "url",
    [
        "https://t.me/+HRBIUUTaR-hhOTRi",
        "https://t.me/joinchat/AAAAAE1234",
        "https://t.me/MLunderhood",
        "https://t.me/s/MLunderhood",
        "https://telegram.me/some_channel",
    ],
)
def test_a_link_to_a_channel_is_not_part_of_the_item(url):
    """A forwarded post's subscribe link is about the channel, never about the item."""
    assert exclusion_reason(url, url) == "telegram-channel"


@pytest.mark.parametrize(
    "url",
    [
        "https://t.me/MLunderhood/1234",
        "https://t.me/s/MLunderhood/1234",
    ],
)
def test_a_link_to_one_post_inside_a_channel_is_content(url):
    assert exclusion_reason(url, url) is None


def test_a_bare_domain_named_in_prose_is_not_a_link_the_user_shared():
    """Telegram marks bare domains in pasted text as link entities."""
    assert exclusion_reason("BIKEPACKING.com", "https://bikepacking.com") == "bare-hostname"
    assert exclusion_reason("Reddit.com", "https://reddit.com") == "bare-hostname"


def test_a_schemeless_url_with_a_path_is_still_a_link():
    assert exclusion_reason("youtu.be/QOrlzrnfJfs", "https://youtu.be/QOrlzrnfJfs") is None
    assert exclusion_reason("https://example.com", "https://example.com") is None


def test_excluded_rows_stay_in_the_table_but_never_earn_a_priority():
    payloads = [
        {
            "message_id": 1,
            "date": 1,
            "text": "paper channel",
            "entities": [
                {"type": "text_link", "offset": 0, "length": 5, "url": "https://arxiv.org/abs/1"},
                {"type": "text_link", "offset": 6, "length": 7, "url": "https://t.me/+invite"},
            ],
        }
    ]
    table = build_link_table(payloads, "")

    assert [(entry.status, entry.reason) for entry in table] == [
        ("discovered", None),
        ("excluded", "telegram-channel"),
    ]
    # The surviving row is the only link, so it earns the sole-link priority.
    assert table[0].priority == 2
