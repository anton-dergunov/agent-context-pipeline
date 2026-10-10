"""What the owner's paper library knows about the papers an item names.

The laptop may hold a paper library (the separate `agentic-paper-library` project), whose
`paperlib info` reports on a paper in one call: whether the library has it and where its
markdown, note and overview are, or, for a paper outside it, its venue, citations and the
nearest papers held. `sync.py` asks it about the papers of the annotated route and appends
the answer to each item's `index.md` as a delimited section, so the routing agent starts
from it.

Standard library only, and nothing here imports the daemon or `sync.py`: a laptop
without a paper library never runs any of it. `paperlib info --json` is the interface;
its keys are read in `_entry` and nowhere else.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

COMMAND = "paperlib"
#: An item naming more arXiv papers than this beside its own is a reading list. Each
#: paper outside the library costs a model request, and the routing outcome for a pile
#: of links is one task, which the fortieth paper's citations cannot change.
MAX_PAPERS = 3
#: Seconds allowed for the command to start, and for each paper it is asked about. A
#: saved answer takes none; a new paper takes two model requests.
TIMEOUT_BASE = 60
TIMEOUT_PER_PAPER = 90

ARXIV_ID = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf|html)/|research-arxiv-|arXiv\.)(\d{4}\.\d{4,5})", re.I
)

BLOCK_BEGIN = "<!-- paper-info:begin -->"
BLOCK_END = "<!-- paper-info:end -->"
BLOCK = re.compile(rf"\n*{re.escape(BLOCK_BEGIN)}.*?{re.escape(BLOCK_END)}\n*", re.S)
#: What the section goes in front of, first match wins: the possible-neighbours block
#: (`neighbours.BLOCK_BEGIN`, repeated here because that module needs the optional
#: extra to import) and `## Problems`, which stays last.
FOLLOWING = ("\n<!-- neighbours:begin -->", "\n## Problems\n")
NOTICE = (
    "What `paperlib info` reports: public records and the owner's paper library, read\n"
    "from the abstract alone. A report, not a judgement of the item."
)
#: Said once per section, so that each paper's files are short relative paths.
LIBRARY_LINE = "The library is `{library}`; the paths below are inside it unless absolute."

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class PaperInfoUnavailable(RuntimeError):
    """The paper library cannot be asked on this machine."""


@dataclass(frozen=True)
class Wanted:
    """The papers one item is asked about."""

    #: The arXiv id of the paper the item is, when it is one.
    own: str | None = None
    #: The other arXiv papers it names, in the order it names them.
    others: tuple[str, ...] = ()
    #: The item's title, when it is a paper with no arXiv id: only the library has it.
    title: str | None = None

    @property
    def queries(self) -> tuple[str, ...]:
        return tuple(query for query in (self.own, *self.others, self.title) if query)


def _normalised(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", title.lower()).split())


def wanted(fields: Mapping[str, str], listing: str) -> Wanted:
    """Which papers to ask about, from an index's frontmatter and its Sources and Links.

    A paper item's own paper always. The other arXiv papers it names only while they are
    few (`MAX_PAPERS`); past that, none of them.
    """
    own = None
    title = None
    if fields.get("kind") == "paper":
        found = ARXIV_ID.search(f"{fields.get('canonical_url', '')} {fields.get('doi', '')}")
        own = found.group(1) if found else None
        title = None if own else fields.get("title") or None
    others = tuple(dict.fromkeys(i for i in ARXIV_ID.findall(listing) if i != own))
    return Wanted(own, others if len(others) <= MAX_PAPERS else (), title)


def lookup(
    queries: Sequence[str], library: Path, run: Runner = subprocess.run
) -> list[dict[str, object]]:
    """Ask the library about every query in one call. Returns the records it printed.

    One process for the whole queue: the command answers a paper it holds or has looked
    at before from disk, and only a new one costs model requests. Whatever it printed
    before failing or running out of time is kept.
    """
    command = shutil.which(COMMAND)
    if command is None:
        raise PaperInfoUnavailable(f"`{COMMAND}` is not on the PATH")
    try:
        output = run(
            [command, "-C", str(library), "info", "--json", *queries],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_BASE + TIMEOUT_PER_PAPER * len(queries),
        ).stdout
    except subprocess.TimeoutExpired as expired:
        output = expired.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
    records = []
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("status") in {
            "in library",
            "not in library",
            "skipped",
        }:
            records.append(record)
    return records


def answers(
    item: Wanted, records: Sequence[Mapping[str, object]]
) -> list[tuple[Mapping[str, object], bool]]:
    """The records that answer ITEM, each with whether it is the item's own paper."""
    by_id = {str(record.get("arxiv")): record for record in records if record.get("arxiv")}
    found = [(by_id[i], i == item.own) for i in (item.own, *item.others) if i in by_id]
    if item.title:
        title = _normalised(item.title)
        found += [(r, True) for r in records if _normalised(str(r.get("title") or "")) == title][:1]
    return found


def _joined(*parts: str | None) -> str:
    return " · ".join(part for part in parts if part)


def _path(value: object) -> str:
    return f"`{value}`"


def _entry(record: Mapping[str, object], own: bool) -> list[str]:
    """One paper's lines. OWN leaves out what the item's index already says of it: the
    date in its frontmatter and the abstract quoted as its Lead."""
    get = record.get
    arxiv = f" — arXiv {get('arxiv')}" if get("arxiv") else ""
    lines = [f"**{' '.join(str(get('title') or '').split())}**{arxiv}"]
    add = lambda name, value: lines.append(f"- {name}: {value}") if value else None  # noqa: E731
    status = get("status")
    if status == "in library":
        add("Status", "in the library" + (f", filed in `{get('folder')}`" if get("folder") else ""))
        for name in ("paper", "note", "overview", "review"):
            add(name.capitalize(), _path(get(name)) if get(name) else None)
        add("Summary", get("summary"))
        return lines
    if status == "skipped":
        skipped = get("skipped") if isinstance(get("skipped"), dict) else {}
        add("Status", f"skipped earlier ({skipped.get('date')}): {skipped.get('reason')}")
        return lines
    add("Status", "not in the library")
    if not own:
        add("Submitted", get("published"))
    cited = get("citations") is not None
    add(
        "Venue",
        _joined(str(get("venue") or ("arXiv only" if cited else "")), str(get("comment") or "")),
    )
    if cited:
        rate = (
            f", {get('citations_per_year'):g} a year"
            if get("citations_per_year") is not None
            else ""
        )
        add(
            "Citations",
            f"{get('citations')} in total{rate}, {get('influential_citations')} where the "
            f"citing paper builds on this one · uptake {get('uptake')}",
        )
    named = get("named_in_library") if isinstance(get("named_in_library"), dict) else {}
    if named.get("papers") or named.get("notes_and_reviews"):
        add(
            "Named by",
            f"{named.get('papers')} library papers, {named.get('notes_and_reviews')} notes or reviews",
        )
    if "proposes" in record:
        folder = f"`{get('folder')}` ({get('folder_papers')} papers)" if get("folder") else None
        kind = ", ".join(str(part) for part in (get("type"), get("character")) if part)
        add("Would be filed in", _joined(folder, f"type {kind}" if kind else None))
        if not own:
            add("Proposes", get("proposes"))
            add("Evidence", get("evidence"))
        releases = str(get("releases") or "")
        add("Releases", None if releases.lower().startswith("none") else releases)
        nearest = get("nearest") if isinstance(get("nearest"), list) else []
        for near in nearest:
            if isinstance(near, dict):
                add(
                    "Nearest held",
                    f"{near.get('title')} [{near.get('relation')}] — "
                    f"{_path(near.get('paper'))}: {near.get('how')}",
                )
    return lines


def render_block(found: Sequence[tuple[Mapping[str, object], bool]], library: Path) -> str:
    """The section for one item, or "" when the library said nothing about its papers."""
    if not found:
        return ""
    lines = [BLOCK_BEGIN, "", "## Paper library", "", NOTICE, LIBRARY_LINE.format(library=library)]
    for record, own in found:
        lines += ["", *_entry(record, own)]
    lines += ["", BLOCK_END]
    return "\n".join(lines)


def apply_block(index_text: str, block: str) -> str:
    """Return INDEX_TEXT with BLOCK as its only paper-library section.

    Stripped, then inserted: idempotent, and regenerated whole on every sync. It lands
    after `## Links`, before the possible neighbours and `## Problems`.
    """
    text = BLOCK.sub("\n\n", index_text).rstrip("\n")
    if not block:
        return text + "\n"
    positions = [text.find(marker) for marker in FOLLOWING]
    position = min((p for p in positions if p != -1), default=-1)
    if position == -1:
        return f"{text}\n\n{block.strip()}\n"
    return f"{text[:position].rstrip()}\n\n{block.strip()}\n{text[position:].rstrip()}\n"
