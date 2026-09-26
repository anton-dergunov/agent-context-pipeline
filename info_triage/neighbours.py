"""Nearest passages in the Org plans and the Obsidian vault for one inbox item.

Laptop-side enrichment, run by `sync.py` after the items are already delivered. It
must not import the daemon: `sync.py` is deliberately a separate program.

The shape here is measured, not chosen — see `docs/Related-notes-design.md`:

- BM25 recall, no vector index. An embedding earns nothing on this corpus (§3a): BM25
  alone puts the gold unit in the rerank pool 15/15 times.
- One BM25 index per corpus, never a shared one (§6b). Sharing IDF and average
  document length lets vault growth move Org-side ranking, for no reason at all.
- A cross-encoder rerank against a *short* query, scored as a raw logit. The long
  retrieval query saturates the cross-encoder at 1.0 for every candidate, and the
  sigmoid saturates at both ends, leaving nothing for the abstain gate to read (§3).
- At most two rows per corpus. Precision falls off a cliff after rank 1–2, so a third
  row costs a verification read and buys nothing (§4).
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Org files skipped when the caller names none: the Emacs configuration's per-folder
#: settings, which are not plans. `sync.py` passes the laptop's `org_exclude` setting
#: instead, whose default mirrors this.
ORG_SKIP = frozenset({"workspace.org", "init.org"})
#: Vault directories holding machinery, attachments or templates rather than notes.
VAULT_SKIP_DIRS = {
    ".git",
    ".obsidian",
    ".smart-env",
    ".trash",
    "image",
    "scripts",
    "Templates",
    "Bases",
}
KEYWORDS = {"TODO", "NEXT", "MAYB", "WAIT", "DONE", "STARTED", "INPR", "HOLD", "CANCELLED"}
HEADING = re.compile(r"^(\*+)\s+(.*)$")
ORG_TAGS = re.compile(r"\s+(:[A-Za-z0-9_@#%:]+:)\s*$")
ORG_PRIORITY = re.compile(r"^\[#[A-C]\]\s*")
ORG_DRAWER = re.compile(r"^\s*(SCHEDULED:|DEADLINE:|CLOSED:|:PROPERTIES:|:END:|:[A-Z_]+:)")
MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

STOP = set(
    """a an the and or but if then than that this these those of in on at to for from by
with without as is are was were be been being it its it's he she they them their there here
what which who whom how why when where all any both each few more most other some such no nor
not only own same so too very can will just don should now about into over under again further
i you your my we our us me him her his do does did doing have has had having would could may
might must shall me one two also new use used using make makes made get gets got like via per
you're we're isn't doesn't""".split()
)
#: A token may carry `.`, `#`, `+` and `-` so that `gpt-4.1`, `c#` and `node.js`
#: survive. The cost is that a word ending a sentence keeps its full stop and is a
#: different term from the same word mid-sentence. That is what every number in
#: `docs/Related-notes-design.md` was measured with; re-measure before changing it.
TOKEN = re.compile(r"[a-z0-9][a-z0-9+.#_-]*")

#: How many BM25 hits per corpus reach the reranker. 20 + 20 = the measured pool of 40.
POOL = 20
#: Rows per corpus. Two, because precision@3 is ~40%.
ROWS = 2
#: Abstain below this raw logit. Fitted to 22 hand-judged queries: both out-of-scope
#: controls fall well under it and ~88% of what clears it is correct.
CUT = -3.0
#: A confident hit, as opposed to one worth a look.
STRONG = 0.0
#: Characters of a candidate the reranker sees. Its window is 384 tokens anyway.
CANDIDATE_CHARS = 1500
#: Words of the retrieval query that reach the *rerank* query. See the module docstring.
RERANK_WORDS = 28

MODEL = "BAAI/bge-reranker-base"
#: 278M XLM-R base. Multilingual, because the notes contain Russian and Spanish.
MAX_LENGTH = 384

BLOCK_BEGIN = "<!-- neighbours:begin -->"
BLOCK_END = "<!-- neighbours:end -->"
#: Matches a previously written block with the blank lines around it, so that
#: re-annotating replaces rather than accumulates.
BLOCK = re.compile(rf"\n*{re.escape(BLOCK_BEGIN)}.*?{re.escape(BLOCK_END)}\n*", re.S)
PROBLEMS_HEADING = "\n## Problems\n"

#: The wording is load-bearing and measured (design §6). The identical rows under a
#: bare `## Related` heading cost 1.3% *more* tokens than no field at all and drove the
#: agent to `MERGE` every item; this version cut tokens 15.5% and restored a correct
#: mixed verdict set. Do not tidy it.
CAVEAT = (
    "Machine retrieval, not a finding. These are the nearest passages a search over the\n"
    "plans and the vault found; roughly one in eight is wrong, and a hit here is *not*\n"
    "evidence the item is a duplicate. Judge `vet` first and on the item's own merits,\n"
    "then open these to check whether they actually cover it. An item with a neighbour is\n"
    "as likely to need a new task beside it as a merge into it."
)


@dataclass(frozen=True)
class Unit:
    """One retrievable passage of one corpus."""

    kind: str  # org-task | org-section | org-charter | vault-section
    source: str  # "org" | "vault"
    file: str  # corpus-relative path
    line: int  # 1-indexed start line
    path: str  # human breadcrumb
    title: str
    keyword: str | None
    text: str  # what is indexed


@dataclass(frozen=True)
class Neighbour:
    """One emitted row."""

    source: str  # "plan" | "vault"
    file: str
    line: int
    path: str
    title: str
    score: float  # raw cross-encoder logit


#: Query and candidate bodies in, one logit per candidate out. Injected in tests so the
#: suite never downloads a model.
Scorer = Callable[[str, Sequence[str]], Sequence[float]]
#: `total == 0` announces a stage; otherwise this is item `done` of `total`.
Progress = Callable[[str, int, int], None]


def _org_units(lines: list[str], rel: str) -> Iterable[Unit]:
    """Yield the charter and every heading of one Org file."""
    first = next((i for i, line in enumerate(lines) if HEADING.match(line)), len(lines))

    # The charter: a file's `#+SUBTITLE:` and its opening prose. Indexed because it is
    # a destination signal, but never emitted — a charter is not a duplicate of anything.
    charter = [line for line in lines[:first] if line.strip()]
    subtitle = next(
        (
            line.split(":", 1)[1].strip()
            for line in charter
            if line.upper().startswith("#+SUBTITLE:")
        ),
        "",
    )
    title = next(
        (line.split(":", 1)[1].strip() for line in charter if line.upper().startswith("#+TITLE:")),
        rel,
    )
    prose = " ".join(line for line in charter if not line.startswith("#+"))
    if prose or subtitle:
        body = f"{subtitle}. {prose}".strip()
        yield Unit("org-charter", "org", rel, 1, rel, title, None, f"{title}. {body}")

    stack: list[str] = []
    current: dict | None = None
    body_lines: list[str] = []

    def flush() -> Iterable[Unit]:
        if current is None:
            return
        kept = [line for line in body_lines if not ORG_DRAWER.match(line)]
        body = " ".join(line.strip() for line in kept).strip()
        crumb = " > ".join([rel] + current["ancestors"])
        kind = "org-task" if current["keyword"] else "org-section"
        yield Unit(
            kind,
            "org",
            rel,
            current["line"],
            crumb,
            current["title"],
            current["keyword"],
            f"{crumb} > {current['title']}. {body}".strip(),
        )

    for number, line in enumerate(lines[first:], start=first + 1):
        match = HEADING.match(line)
        if not match:
            if current is not None:
                body_lines.append(line)
            continue
        yield from flush()
        level = len(match.group(1))
        rest = match.group(2)
        keyword = None
        parts = rest.split(None, 1)
        if parts and parts[0] in KEYWORDS:
            keyword = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
        rest = ORG_PRIORITY.sub("", rest)
        tags = ORG_TAGS.search(rest)
        heading = (rest[: tags.start()] if tags else rest).strip()
        del stack[level - 1 :]
        current = {"line": number, "title": heading, "keyword": keyword, "ancestors": list(stack)}
        stack.append(heading)
        body_lines = []
    yield from flush()


def parse_org(path: Path, rel: str) -> list[Unit]:
    """Parse one Org plan file into a charter unit and one unit per heading."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return list(_org_units(lines, rel))


def parse_vault(path: Path, rel: str, max_words: int = 280) -> list[Unit]:
    """Parse one vault note into one unit per section, windowed at MAX_WORDS.

    Each window keeps the line it actually starts on, so two windows of one long
    section never collapse onto the same citation.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    note = path.stem
    folder = str(Path(rel).parent)
    units: list[Unit] = []

    marks = [(0, note)]
    for number, line in enumerate(lines):
        match = MD_HEADING.match(line)
        if match and len(match.group(1)) <= 3:
            marks.append((number, match.group(2).strip()))
    marks.append((len(lines), ""))

    def is_body(line: str) -> bool:
        return bool(line.strip()) and not MD_HEADING.match(line) and not line.startswith("---")

    for (start, heading), (end, _) in zip(marks, marks[1:]):
        segment = lines[start:end]
        words = " ".join(line.strip() for line in segment if is_body(line)).split()
        if not words:
            continue
        crumb = f"{folder}/{note}" + (f" > {heading}" if heading != note else "")
        # Where each body line begins, in words, so a window can name its own line.
        offsets: list[tuple[int, int]] = []
        seen = 0
        for offset, line in enumerate(segment):
            if is_body(line):
                offsets.append((seen, start + offset + 1))
                seen += len(line.split())
        for first_word in range(0, len(words), max_words):
            chunk = " ".join(words[first_word : first_word + max_words])
            line = next(
                (
                    number
                    for words_before, number in reversed(offsets)
                    if words_before <= first_word
                ),
                start + 1,
            )
            units.append(
                Unit("vault-section", "vault", rel, line, crumb, heading, None, f"{crumb}. {chunk}")
            )
    return units


def build_corpus(
    org_root: Path, obsidian_root: Path, org_exclude: Collection[str] = ORG_SKIP
) -> dict[str, list[Unit]]:
    """Parse both corpora, keyed by source. Unreadable files are skipped, not fatal.

    `org_exclude` names Org files to leave out, matched by file name at any depth.
    """
    units: dict[str, list[Unit]] = {"org": [], "vault": []}
    for path in sorted(org_root.rglob("*.org")):
        if ".git" in path.parts or path.name in org_exclude:
            continue
        try:
            units["org"] += parse_org(path, str(path.relative_to(org_root)))
        except OSError:
            continue
    for path in sorted(obsidian_root.rglob("*.md")):
        if any(directory in path.parts for directory in VAULT_SKIP_DIRS):
            continue
        try:
            units["vault"] += parse_vault(path, str(path.relative_to(obsidian_root)))
        except OSError:
            continue
    return units


def tokenize(text: str) -> list[str]:
    return [token for token in TOKEN.findall(text.lower()) if token not in STOP and len(token) > 1]


class BM25:
    """Okapi BM25 over pre-tokenized documents."""

    def __init__(self, documents: Sequence[Sequence[str]], k1: float = 1.2, b: float = 0.6):
        self.k1, self.b = k1, b
        self.count = len(documents)
        self.lengths = np.array([len(document) for document in documents], dtype=np.float32)
        self.average = float(self.lengths.mean()) if self.count else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for position, document in enumerate(documents):
            for term, frequency in Counter(document).items():
                self.postings[term].append((position, frequency))
        self.idf = {
            term: math.log(1 + (self.count - len(posting) + 0.5) / (len(posting) + 0.5))
            for term, posting in self.postings.items()
        }

    def score(self, query: Sequence[str]) -> np.ndarray:
        scores = np.zeros(self.count, dtype=np.float32)
        if not self.count:
            return scores
        for term in set(query):
            posting = self.postings.get(term)
            if not posting:
                continue
            index = np.fromiter((i for i, _ in posting), dtype=np.int64, count=len(posting))
            frequency = np.fromiter((f for _, f in posting), dtype=np.float32, count=len(posting))
            denominator = frequency + self.k1 * (
                1 - self.b + self.b * self.lengths[index] / self.average
            )
            scores[index] += self.idf[term] * (frequency * (self.k1 + 1)) / denominator
        return scores


@dataclass(frozen=True)
class Index:
    """Both corpora, each with its own BM25 index.

    Separate indices are the point: they keep Org-side ranking exactly invariant as the
    vault grows, which a shared index does not (design §6b).
    """

    units: dict[str, list[Unit]]
    indices: dict[str, BM25]

    @property
    def size(self) -> int:
        return sum(len(units) for units in self.units.values())


def build_index(
    org_root: Path, obsidian_root: Path, org_exclude: Collection[str] = ORG_SKIP
) -> Index:
    units = build_corpus(org_root, obsidian_root, org_exclude)
    return Index(
        units=units,
        indices={
            source: BM25([tokenize(unit.text) for unit in source_units])
            for source, source_units in units.items()
        },
    )


def short(query: str, words: int = RERANK_WORDS) -> str:
    """The item's title and first sentence — what the reranker is asked about.

    A multi-topic query makes every candidate look relevant and saturates the model at
    1.0, destroying exactly the ordering the rerank exists to produce.
    """
    collected: list[str] = []
    seen = 0
    for sentence in re.split(r"(?<=[.!?])\s", query):
        collected.append(sentence)
        seen += len(sentence.split())
        if seen >= words:
            break
    return " ".join(collected)


def default_scorer(device: str = "cpu") -> Scorer:
    """Load the cross-encoder and return a scoring callable.

    Raises ImportError when the `neighbours` extra is not installed; the caller treats
    that as "skip annotation", never as a failure.
    """
    import torch
    from sentence_transformers import CrossEncoder

    torch.set_num_threads(6)
    model = CrossEncoder(MODEL, device=device, max_length=MAX_LENGTH)

    def score(query: str, documents: Sequence[str]) -> Sequence[float]:
        # Raw logits, not the sigmoid: the probability saturates at both ends, and the
        # abstain gate needs a score that still moves when the model is merely unsure.
        raw = model.predict(
            [(query, document) for document in documents],
            batch_size=16,
            show_progress_bar=False,
            activation_fn=torch.nn.Identity(),
        )
        return np.asarray(raw, dtype=np.float32).reshape(-1).tolist()

    return score


def neighbours_for(
    index: Index,
    retrieval_query: str,
    *,
    scorer: Scorer,
    k: int = ROWS,
    pool: int = POOL,
    cut: float = CUT,
) -> tuple[str | None, list[Neighbour]]:
    """Return (destination file, rows) for one item.

    The destination is the file of the top-ranked plan row — a guess about the *file*,
    which is a safer call than the task, and ~90% accurate. Deriving it from the file
    charters instead was tried and measurably fails.
    """
    query_terms = tokenize(retrieval_query)
    candidates: list[Unit] = []
    for source, bm25 in index.indices.items():
        scores = bm25.score(query_terms)
        for position in np.argsort(-scores)[:pool]:
            if scores[position] > 0:
                candidates.append(index.units[source][int(position)])
    if not candidates:
        return None, []

    logits = scorer(short(retrieval_query), [unit.text[:CANDIDATE_CHARS] for unit in candidates])
    ranked = sorted(zip(candidates, logits), key=lambda pair: -pair[1])

    destination: str | None = None
    rows: list[Neighbour] = []
    per_bucket: Counter = Counter()
    per_file: Counter = Counter()
    for unit, score in ranked:
        if score < cut:
            break
        if unit.kind == "org-charter":
            continue
        bucket = "plan" if unit.source == "org" else "vault"
        if destination is None and bucket == "plan":
            destination = unit.file
        if per_bucket[bucket] >= k or per_file[unit.file]:
            continue
        per_bucket[bucket] += 1
        per_file[unit.file] += 1
        rows.append(
            Neighbour(bucket, unit.file, unit.line, unit.path, unit.title, round(float(score), 2))
        )
    return destination, rows


def _trail(row: Neighbour) -> str:
    """The breadcrumb without its file, which the citation beside it already names."""
    crumb = [part.strip() for part in row.path.split(" > ")][1:]
    if not crumb or crumb[-1] != row.title:
        crumb.append(row.title)
    return " > ".join(part for part in crumb if part)


def render_block(destination: str | None, rows: Sequence[Neighbour]) -> str:
    """Render the delimited section, or "" when nothing cleared the cut.

    A list, never a table: both this and `## Sources` are read in a half-width editor
    window beside the agent, and a table cannot be narrowed.
    """
    if not rows:
        return ""
    lines = [BLOCK_BEGIN, "", "## Possible neighbours — unverified", ""]
    if destination:
        lines += [
            f"Most likely destination file: `{destination}` (from the nearest neighbour below — a",
            "guess about the *file*, which is a safer call than the task).",
            "",
        ]
    lines += [CAVEAT, ""]
    for row in rows:
        strength = "strong" if row.score >= STRONG else "likely"
        lines.append(f"- {row.source:<5} `{row.file}:{row.line}` {strength} — {_trail(row)}")
    lines += ["", BLOCK_END]
    return "\n".join(lines)


def apply_block(index_text: str, block: str) -> str:
    """Return INDEX_TEXT with BLOCK as its only neighbours section.

    Any previous block is stripped first, so this is idempotent and the section is
    fully regenerated on every sync rather than patched. It lands after `## Links` and
    before `## Problems`, which stays last.
    """
    text = BLOCK.sub("\n\n", index_text).rstrip("\n")
    if not block:
        return text + "\n"
    position = text.find(PROBLEMS_HEADING)
    if position == -1:
        return f"{text}\n\n{block.strip()}\n"
    return f"{text[:position].rstrip()}\n\n{block.strip()}\n{text[position:].rstrip()}\n"


def annotate(
    queries: Sequence[tuple[str, str]],
    org_root: Path,
    obsidian_root: Path,
    *,
    org_exclude: Collection[str] = ORG_SKIP,
    scorer: Scorer | None = None,
    progress: Progress | None = None,
) -> dict[str, str]:
    """Return one rendered block per item that has neighbours, keyed as given.

    Pure and in-memory: nothing here reads or writes an item. The caller has already
    read every index it cares about, which is what lets the user work the queue while
    this runs.
    """

    def report(label: str, done: int = 0, total: int = 0) -> None:
        if progress is not None:
            progress(label, done, total)

    report("parsing the plans and the vault")
    index = build_index(org_root, obsidian_root, org_exclude)
    report(f"{index.size} passages indexed")
    if scorer is None:
        report("loading the reranker")
        scorer = default_scorer()

    blocks: dict[str, str] = {}
    for position, (key, query) in enumerate(queries, start=1):
        report(key, position, len(queries))
        if not query.strip():
            continue
        destination, rows = neighbours_for(index, query, scorer=scorer)
        block = render_block(destination, rows)
        if block:
            blocks[key] = block
    return blocks


def _dry_run(route_dir: Path) -> int:
    """Print what would be written for one route's items. Writes nothing."""
    from info_triage.sync import default_config, neighbour_query

    config = default_config()
    if config.org_root is None or config.obsidian_root is None:
        print("No corpus roots configured", file=sys.stderr)
        return 1
    index = build_index(config.org_root, config.obsidian_root, config.org_exclude)
    print(f"{index.size} passages indexed")
    scorer = default_scorer()
    for item in sorted(path for path in route_dir.iterdir() if path.is_dir()):
        index_path = item / "index.md"
        if not index_path.is_file():
            continue
        query = neighbour_query(index_path.read_text(encoding="utf-8"))
        destination, rows = neighbours_for(index, query, scorer=scorer)
        print(f"\n=== {item.name}  destination={destination}\n    {short(query)[:110]}")
        for row in rows:
            print(f"  {row.source:<5} {row.score:6.2f}  {row.file}:{row.line}  {_trail(row)[:70]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m info_triage.neighbours <inbox-route-directory>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(_dry_run(Path(sys.argv[1]).expanduser()))
