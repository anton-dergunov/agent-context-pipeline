from info_triage.rendering import (
    apply_text_link_entities,
    entity_slice,
    payload_content,
    render_capture_payloads,
)


def text_link(offset, length, url):
    return {"type": "text_link", "offset": offset, "length": length, "url": url}


def test_hidden_hyperlink_destinations_become_markdown_links():
    text = "Diffusion explainer\nGithub"
    entities = [
        text_link(0, 19, "https://poloclub.github.io/diffusion-explainer/"),
        text_link(20, 6, "https://github.com/poloclub/diffusion-explainer"),
    ]

    assert apply_text_link_entities(text, entities) == (
        "[Diffusion explainer](https://poloclub.github.io/diffusion-explainer/)\n"
        "[Github](https://github.com/poloclub/diffusion-explainer)"
    )


def test_offsets_are_counted_in_utf16_code_units():
    # The rocket is one Python character but two UTF-16 units, so a naive
    # implementation would place the link one character to the left.
    text = "🚀 Paper here"
    entities = [text_link(9, 4, "https://arxiv.org/abs/2305.03509")]

    assert apply_text_link_entities(text, entities) == (
        "🚀 Paper [here](https://arxiv.org/abs/2305.03509)"
    )


def test_entity_slice_rejects_spans_outside_the_text():
    assert entity_slice("short", 0, 5) == "short"
    assert entity_slice("short", 3, 9) is None
    assert entity_slice("short", -1, 2) is None
    assert entity_slice("short", 0, 0) is None
    assert entity_slice("short", True, 2) is None


def test_labels_and_destinations_are_escaped():
    text = "click [me]"
    entities = [text_link(0, 10, "https://example.com/a(b)c")]

    assert apply_text_link_entities(text, entities) == (
        "[click \\[me\\]](https://example.com/a\\(b\\)c)"
    )


def test_overlapping_and_unusable_entities_are_skipped():
    text = "one two\nthree"
    entities = [
        text_link(0, 3, "https://example.com/one"),
        text_link(1, 3, "https://example.com/overlap"),
        text_link(4, 9, "https://example.com/across-a-line-break"),
        text_link(4, 3, "ftp://example.com/wrong-scheme"),
        {"type": "url", "offset": 4, "length": 3},
        {"type": "bold", "offset": 4, "length": 3},
    ]

    assert apply_text_link_entities(text, entities) == ("[one](https://example.com/one) two\nthree")


def test_payload_content_uses_caption_entities_for_captions():
    payload = {
        "message_id": 1,
        "date": 100,
        "caption": "Watch this",
        "caption_entities": [text_link(6, 4, "https://youtu.be/Zg4bClA8m2c")],
    }

    assert payload_content(payload) == "Watch [this](https://youtu.be/Zg4bClA8m2c)"


def test_rendered_segments_carry_entity_destinations():
    payloads = [
        {
            "message_id": 1,
            "date": 100,
            "text": "Статья",
            "entities": [text_link(0, 6, "https://arxiv.org/abs/2305.03509")],
        }
    ]

    assert render_capture_payloads(payloads) == (
        "## Segment 1 — text\n\n[Статья](https://arxiv.org/abs/2305.03509)"
    )
