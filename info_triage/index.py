"""Render `index.md` — the one file the routing agent has to read.

Everything here is derived from what capture and the earlier steps already know:
the retained payloads, the rendered body, and the link table. Nothing is fetched
and nothing is summarized. Fields that need content extraction (title, authors,
published, venue, doi, sources, and the `## Sources` and `## Lead` sections) are
deliberately absent until extraction exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .extraction import CONTENT_NAME, ExtractionRecord
from .models import LinkTableEntry
from .rendering import SEGMENT_HEADING_RE, is_forwarded, payload_order, segment_kind
from .utilities.markdown import escape_markdown_destination, escape_markdown_label
from .utilities.url_resolution import iter_link_occurrences

# A note is a remark, not prose: the real examples in the design run to a line or
# two. Anything longer is the shared material itself, and attributing it as the
# user's intent would mislead the reader that trusts the field.
SHORT_NOTE_CHARS = 200

NO_NOTE_TEXT = "[forwarded — no note of your own]"
VOICE_PROVENANCE = "— transcribed from a Telegram voice message"

# `kind` values that make the abstract, not the opening prose, the right lead.
ABSTRACT_KINDS = frozenset({"paper"})

# Handlers whose name is also the item's origin. Everything else is `web`.
ORIGIN_HANDLERS = frozenset({"instagram", "linkedin", "medium", "youtube"})
# Link statuses that mean the resolver never produced a title for the row.
UNFINISHED_STATUSES = frozenset({"unresolved", "skipped"})


@dataclass(frozen=True)
class IndexSegment:
    """One rendered Telegram segment, paired back with its payload's facts."""

    number: int
    kind: str
    forwarded: bool
    text: str

    @property
    def has_url(self) -> bool:
        return any(True for _ in iter_link_occurrences(self.text))


def _split_body(body: str) -> list[str] | None:
    """Split a rendered body on its segment headings, or None if it has none."""
    parts: list[list[str]] = []
    for line in body.splitlines():
        if SEGMENT_HEADING_RE.fullmatch(line):
            parts.append([])
        elif parts:
            parts[-1].append(line)
        elif line.strip():
            return None
    if not parts:
        return None
    return ["\n".join(lines).strip() for lines in parts]


def build_segments(payloads: Sequence[dict[str, Any]], body: str) -> list[IndexSegment] | None:
    """Pair each rendered segment with the payload it was rendered from.

    Returns None when the body and the payloads cannot be lined up, which is the
    honest answer: without the pairing there is no forwarding provenance, so no
    intent can be attributed and the whole body is the user's own words.
    """
    texts = _split_body(body)
    if texts is None:
        return None
    try:
        ordered = sorted(payloads, key=payload_order)
    except (KeyError, TypeError, ValueError):
        return None
    if len(ordered) != len(texts):
        return None
    return [
        IndexSegment(number, segment_kind(payload), is_forwarded(payload), text)
        for number, (payload, text) in enumerate(zip(ordered, texts, strict=True), 1)
    ]


def _trailing_note(text: str) -> str | None:
    """Return the text following the last link, when it is short enough to be a note."""
    end = max((occurrence.span_end for occurrence in iter_link_occurrences(text)), default=None)
    if end is None:
        return None
    note = text[end:].strip()
    if not note or len(note) > SHORT_NOTE_CHARS:
        return None
    return note


def _note_candidates(segments: Sequence[IndexSegment]) -> list[tuple[IndexSegment, str]]:
    """Apply the three heuristics from the design's intent-detection section."""
    candidates: list[tuple[IndexSegment, str]] = []
    for index, segment in enumerate(segments):
        # A forwarded segment is somebody else's words and is never the note.
        if segment.forwarded or not segment.text.strip():
            continue
        if segment.has_url:
            note = _trailing_note(segment.text)
            if note is not None:
                candidates.append((segment, note))
            continue
        if len(segment.text.strip()) > SHORT_NOTE_CHARS:
            continue
        neighbours = [
            segments[position]
            for position in (index - 1, index + 1)
            if 0 <= position < len(segments)
        ]
        if any(neighbour.forwarded or neighbour.has_url for neighbour in neighbours):
            candidates.append((segment, segment.text.strip()))
    return candidates


def detect_intent(segments: Sequence[IndexSegment]) -> str | None:
    """Return the user's own note, or None when the heuristics do not agree."""
    candidates = _note_candidates(segments)
    return candidates[0][1] if len(candidates) == 1 else None


# Statuses of rows that stay in links.json for traceability but are not part of
# the item: a repeat of an earlier row, or a link that was never the user's.
UNLISTED_STATUSES = frozenset({"duplicate", "excluded"})


def _listed(links: Sequence[LinkTableEntry]) -> list[LinkTableEntry]:
    return [entry for entry in links if entry.status != "excluded"]


def _distinct(links: Sequence[LinkTableEntry]) -> list[LinkTableEntry]:
    return [entry for entry in links if entry.status not in UNLISTED_STATUSES]


def _primary_link(links: Sequence[LinkTableEntry]) -> LinkTableEntry | None:
    distinct = _distinct(links)
    if not distinct:
        return None
    return min(distinct, key=lambda entry: (entry.priority, entry.n))


def _origin(segments: Sequence[IndexSegment] | None, links: Sequence[LinkTableEntry]) -> str:
    if segments and any(segment.forwarded for segment in segments):
        return "telegram"
    primary = _primary_link(links)
    if primary is not None:
        return primary.handler if primary.handler in ORIGIN_HANDLERS else "web"
    if segments and any(segment.kind == "voice" for segment in segments):
        return "voice"
    return "text"


def _chat_name(chat: Any) -> str | None:
    if not isinstance(chat, dict):
        return None
    username = chat.get("username")
    if isinstance(username, str) and username:
        return f"@{username}"
    for key in ("title", "first_name"):
        value = chat.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _forward_via(payloads: Sequence[dict[str, Any]]) -> str | None:
    """Describe where a forwarded segment came from, for source-quality judgement."""
    for payload in payloads:
        origin = payload.get("forward_origin")
        if isinstance(origin, dict):
            kind = origin.get("type")
            if kind == "channel":
                name = _chat_name(origin.get("chat"))
                if name:
                    return f"{name} (forwarded channel)"
            elif kind == "chat":
                name = _chat_name(origin.get("sender_chat"))
                if name:
                    return f"{name} (forwarded chat)"
            elif kind == "user":
                name = _chat_name(origin.get("sender_user"))
                if name:
                    return f"{name} (forwarded user)"
            elif kind == "hidden_user":
                name = origin.get("sender_user_name")
                if isinstance(name, str) and name.strip():
                    return f"{name.strip()} (forwarded user)"
        chat = payload.get("forward_from_chat")
        name = _chat_name(chat)
        if name:
            label = (
                "channel" if isinstance(chat, dict) and chat.get("type") == "channel" else "chat"
            )
            return f"{name} (forwarded {label})"
        name = _chat_name(payload.get("forward_from"))
        if name:
            return f"{name} (forwarded user)"
        sender_name = payload.get("forward_sender_name")
        if isinstance(sender_name, str) and sender_name.strip():
            return f"{sender_name.strip()} (forwarded user)"
    return None


def _captured_at(metadata: Mapping[str, Any]) -> str | None:
    received_at = metadata.get("received_at")
    if not isinstance(received_at, str):
        return None
    try:
        moment = datetime.fromisoformat(received_at)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kind(
    links: Sequence[LinkTableEntry],
    primary: ExtractionRecord | None,
    linklist_threshold: int,
) -> str | None:
    distinct = _distinct(links)
    if not distinct:
        return "note"
    if len(distinct) >= linklist_threshold:
        return "linklist"
    # Otherwise the item is whatever its best extraction turned out to be.
    return primary.kind if primary is not None else None


def _extraction(
    links: Sequence[LinkTableEntry],
    extractions: Sequence[ExtractionRecord],
) -> tuple[str, str | None]:
    """Roll the per-source outcomes up into the one status /route acts on."""
    if extractions:
        retrieved = [record for record in extractions if record.retrieved]
        if not retrieved:
            return "failed", extractions[0].reason
        incomplete = next((record for record in extractions if record.status != "complete"), None)
        if incomplete is not None:
            return "partial", incomplete.reason
        return "ok", None
    for entry in _distinct(links):
        if entry.status in UNFINISHED_STATUSES:
            return "partial", entry.reason
    return "none", None


def _yaml_scalar(value: str) -> str:
    """Quote arbitrary prose so it cannot break out of one YAML value."""
    collapsed = " ".join(value.split())
    escaped = collapsed.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _yaml_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(_yaml_scalar(value) for value in values) + "]"


def _ranked_extractions(
    extractions: Sequence[ExtractionRecord],
    links: Sequence[LinkTableEntry],
) -> list[ExtractionRecord]:
    """Order the sources the way the link table ranked their targets."""
    priorities = {entry.n: (entry.priority, entry.n) for entry in links}
    return sorted(
        extractions,
        key=lambda record: priorities.get(record.link_n, (99, record.link_n)),
    )


def _thousands(count: int) -> str:
    return f"{count:,}"


def _sources_section(records: Sequence[ExtractionRecord]) -> str:
    """List every extraction with its cost, which is what makes drill-down a choice."""
    lines = []
    for position, record in enumerate(records, 1):
        descriptor = " · ".join(
            part
            for part in (
                record.title,
                ", ".join(record.authors[:3]) or None,
                record.published,
                record.venue,
            )
            if part
        )
        status = record.status
        if record.reason and record.status != "complete":
            status = f"{record.status} (`{record.reason}`)"
        detail = f"`{CONTENT_NAME}` {_thousands(record.word_count)} words"
        if record.via:
            detail += f" · via {record.via}"
        head = f"{position}. `{record.directory}/`"
        if descriptor:
            head += f" — {descriptor}"
        lines.append(f"{head} — {status} · {detail}")
    return "\n".join(lines)


def _truncate_words(text: str, limit: int) -> str:
    """Cut at a paragraph boundary if one is near, otherwise mark the cut."""
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    paragraphs: list[str] = []
    used = 0
    for paragraph in text.split("\n\n"):
        count = len(paragraph.split())
        if paragraphs and used + count > limit:
            break
        paragraphs.append(paragraph)
        used += count
        if used >= limit:
            break
    if paragraphs and used <= limit:
        return "\n\n".join(paragraphs).strip() + ("" if used == len(words) else " …")
    return " ".join(words[:limit]) + " …"


def _forwarded_text(segments: Sequence[IndexSegment] | None) -> str:
    """Return the forwarded material itself, which is otherwise only in capture/."""
    if not segments:
        return ""
    return "\n\n".join(
        segment.text.strip() for segment in segments if segment.forwarded and segment.text.strip()
    )


def _lead(
    record: ExtractionRecord | None,
    segments: Sequence[IndexSegment] | None,
    lead_words: int,
) -> str:
    """Quote the top source: a paper's abstract, or the opening of what was shared.

    Truncation is deliberate and visible. A summary would look complete and stop
    the reader from opening `content.md` when it actually matters.
    """
    if record is not None and record.kind in ABSTRACT_KINDS and record.abstract:
        return record.abstract.strip()
    forwarded = _forwarded_text(segments)
    if forwarded:
        # A forwarded post is the content; without this it lives only in
        # capture/message.md and its links appear to come from nowhere.
        return _truncate_words(forwarded, lead_words)
    if record is not None and record.excerpt:
        return _truncate_words(record.excerpt, lead_words)
    return ""


def _blockquote(text: str) -> str:
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.strip().splitlines())


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|")


def _link_status(entry: LinkTableEntry) -> str:
    if entry.status == "duplicate" and entry.duplicate_of is not None:
        return f"duplicate of {entry.duplicate_of}"
    if entry.reason and entry.status != "resolved":
        return f"{entry.status} ({entry.reason})"
    return entry.status


def _links_table(links: Sequence[LinkTableEntry]) -> str:
    rows = ["| # | link | handler | status |", "|---|------|---------|--------|"]
    for entry in links:
        label = entry.title or entry.label or entry.canonical
        link = (
            f"[{escape_markdown_label(' '.join(label.split()))}]"
            f"({escape_markdown_destination(entry.canonical)})"
        )
        rows.append(
            f"| {entry.n} | {_table_cell(link)} | {_table_cell(entry.handler)} "
            f"| {_table_cell(_link_status(entry))} |"
        )
    return "\n".join(rows)


def _without_segment_headings(body: str) -> str:
    """Drop the segment headings: a transport detail never belongs in the index."""
    kept: list[str] = []
    for line in body.splitlines():
        if SEGMENT_HEADING_RE.fullmatch(line):
            continue
        if not line.strip() and (not kept or not kept[-1].strip()):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _captured_section(
    segments: Sequence[IndexSegment] | None,
    body: str,
    note: tuple[IndexSegment, str] | None,
) -> str:
    if note is not None:
        return _blockquote(note[1]) + _voice_suffix([note[0]])
    if segments is None:
        text = _without_segment_headings(body)
        return _blockquote(text) if text else f"> {NO_NOTE_TEXT}"
    own = [segment for segment in segments if not segment.forwarded and segment.text.strip()]
    if not own:
        return f"> {NO_NOTE_TEXT}"
    text = "\n\n".join(segment.text.strip() for segment in own)
    return _blockquote(text) + _voice_suffix(own)


def _voice_suffix(segments: Sequence[IndexSegment]) -> str:
    """Mark dictated text, so a garbled phrase reads as an ASR artifact."""
    if not any(segment.kind == "voice" for segment in segments):
        return ""
    return f"\n>\n> {VOICE_PROVENANCE}"


def render_index(
    item_id: str,
    metadata: Mapping[str, Any],
    payloads: Sequence[dict[str, Any]],
    body: str,
    links: Sequence[LinkTableEntry],
    extractions: Sequence[ExtractionRecord] = (),
    *,
    linklist_threshold: int,
    lead_words: int = 120,
) -> str:
    """Render one item's complete `index.md`."""
    segments = build_segments(payloads, body)
    note = None
    if segments is not None:
        candidates = _note_candidates(segments)
        note = candidates[0] if len(candidates) == 1 else None

    fields: list[tuple[str, str]] = [("id", item_id)]
    captured_at = _captured_at(metadata)
    if captured_at is not None:
        fields.append(("captured_at", captured_at))
    fields.append(("origin", _origin(segments, links)))
    via = _forward_via(payloads)
    if via is not None:
        fields.append(("via", _yaml_scalar(via)))
    # Always present: an absent note is information, an invented one is damage.
    fields.append(("intent", _yaml_scalar(note[1]) if note is not None else "null"))
    ranked = _ranked_extractions(extractions, links)
    lead_source = ranked[0] if ranked else None
    kind = _kind(links, lead_source, linklist_threshold)
    if kind is not None:
        fields.append(("kind", kind))
    if lead_source is not None:
        if lead_source.title:
            fields.append(("title", _yaml_scalar(lead_source.title)))
        if lead_source.authors:
            fields.append(("authors", _yaml_list(lead_source.authors)))
        for key, value in (
            ("published", lead_source.published),
            ("venue", lead_source.venue),
            ("doi", lead_source.doi),
        ):
            if value:
                fields.append((key, _yaml_scalar(value)))
    primary = _primary_link(links)
    if primary is not None:
        fields.append(("canonical_url", primary.canonical))
    extraction, reason = _extraction(links, extractions)
    fields.append(("extraction", extraction))
    if reason is not None:
        fields.append(("reason", reason))
    if extractions:
        fields.append(("sources", str(len(extractions))))
    link_count = len(_distinct(links))
    if link_count > 5:
        fields.append(("link_count", str(link_count)))

    sections = [
        "---",
        "\n".join(f"{key}: {value}" for key, value in fields),
        "---",
        "",
        "## Captured",
        "",
        _captured_section(segments, body, note),
    ]
    if ranked:
        sections.extend(["", "## Sources", "", _sources_section(ranked)])
    lead = _lead(lead_source, segments, lead_words)
    if lead:
        sections.extend(["", "## Lead", "", _blockquote(lead)])
    listed = _listed(links)
    if listed:
        sections.extend(["", "## Links", "", _links_table(listed)])
    return "\n".join(sections) + "\n"
