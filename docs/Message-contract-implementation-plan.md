# Message contract — implementation plan

Written 2026-08-14. Companion to [`Message-contract-design.md`](Message-contract-design.md)
(the *what* and *why*, accepted in full) and
[`Message-contract-session-notes.md`](Message-contract-session-notes.md) (the evidence).

**How to use this file.** Each `## Session N` below is one Claude Code session.
Paste the fenced *Prompt* block, then let the session read the *Notes* under it
for the decisions and code facts already established — they exist so the session
does not re-derive or re-litigate anything. Sessions are ordered by dependency;
do not reorder 1→4.

The order deliberately follows the recommendation in the session notes §6: get
captures through Telegram → `index.md` → `/route` → an org file **before**
building content extraction. Sessions 1–4 are that thin loop.

---

## Ground rules for all of these sessions

**1. Backward compatibility is forbidden.** Nothing in this repository has been
used for real work. Everything currently captured in Telegram or sitting in
`~/info-triage-inbox/` exists only from testing and is disposable — never write
a migration, a fallback path, a legacy branch, or a compatibility shim for it.
If old items are in the way, delete them. A compatibility layer here is pure
added complexity protecting data nobody wants.

**2. The existing structure is liquid; the tuned behaviour is not.** The current
code is the residue of several independent extraction experiments, not of a
design. Where a name, a file layout, a module boundary or a data shape is wrong
for the contract in the design doc, change it outright rather than wrapping it.
The one thing to preserve is *behaviour that was tuned against real data*:
content retrieval, OCR, and voice transcription. Restructure their output freely;
do not touch how they retrieve or recognize.

**3. But this is not a general refactor.** The scope is the message contract, the
item representation, the processing pipeline, and the design immediately around
them. Command-line design, extractor internals, the web dashboard and similar
are out of scope even where they are visibly imperfect — those are a later,
separate exercise.

---

## Decisions taken here (the six open questions in the session notes §4)

1. **`inbox.org` — deferred to Session 7.** Not because it is risky, but because
   it should be designed together with the wider Emacs review experience rather
   than as an isolated generated file. Ship `inbox.md` first and design the UI
   once the review workflow has been felt.
2. **Budgets go in `config.yaml`** — `extract_budget=5`, `resolve_budget=40`,
   `linklist_threshold=8`, and the per-item wall-clock cap. These are reasoned
   defaults, not measured optima, so they must be tunable without a code change.
3. **The `capture/` restructure happens in full**, with no migration and no
   fallback. Delete the existing test items instead.
4. **`llm_input.txt` is removed, not merely bypassed.** It was designed for a
   summarizing consumer that this design explicitly rejects (§3.6). The three
   `prepare.py` modules that build it become the place where each extractor
   writes the uniform `content.md` / `comments.md` shape instead.
5. **No grouping of `inbox.md` by capture day, and no work on the finding
   ceiling.** Routing is a hand-driven activity: the user selects items by
   judgement, supplies substantial background and intent in the `/route`
   invocation, and reads the report as a whole or in parts as suits the day. A
   capture day is not the unit he operates on. The ~15-finding ceiling stays as
   it is — it exists to keep routing quality high on a manageable batch, it has
   not been measured, and nothing in these sessions should tune or work around
   it. Drop this from consideration entirely.
6. **OCR of Telegram photo attachments → backlog, not implemented.** Instagram
   and YouTube Shorts OCR earn their place because on-screen text is measurably
   where those creators put the content. Whether a forwarded Telegram photo
   carries useful text or noise is untested. Wait for evidence from real use,
   then decide.

---

## Code facts that apply to every session

Found by reading the current tree; recorded once so no session repeats the search.

- **Pipeline mechanics.** Steps are configured in `config.yaml` →
  `processing.steps`, parsed by `info_triage/config.py:_parse_step()` into a
  per-step dataclass, and constructed in
  `info_triage/preprocessing.py:processing_steps_from_config()`. A new step means
  edits in all three places plus `config.py:TRANSFORM_STEP_NAMES` (line 13) and
  the ordering validation near `config.py:348`.
- **Each step gets a fresh temporary workspace** (`processing.py:197`,
  `step-NN/`) and returns `ProcessingStepOutcome` (`succeeded|partial|failed`
  with stable kebab-case reason keys). Files are handed over as
  `GeneratedFile(relative_path, source_path)` on `ProcessingResult`; the source
  must live inside the workspace (`processing.py:246`).
- **Nested generated paths already work.** `storage.py:promote_if_current()`
  does `destination.parent.mkdir(parents=True)`, so
  `extracted/01-arxiv-…/content.md` commits without changes.
- **`ProcessingResult` (`models.py`) carries only `message_markdown`,
  `source_markdown`, `generated_files`.** The link table needs a carrier —
  a typed field is the simpler shape. Decided in Session 2.
- **There is no reuse of a previous revision's output.** `promote_if_current()`
  requires an empty inbox path and the item is re-materialized from
  `telegram.json` on every edit. So the design's "idempotent, revision-keyed"
  extraction (§3.1) **cannot** live inside the item directory — it needs a cache
  outside it, keyed on canonical URL (e.g. `data/extraction-cache/<hash>/`).
  This is the most important structural finding; it lands in Session 5.
- **`message.md` is written at the item root in three places** —
  `storage.py:_write_item()`, `storage.py:promote_if_current()`, and the
  recapture path around `storage.py:697-714` — and read by
  `sync.py:_local_item()` (line ~243). `storage.py:render_message()` prepends the
  category front matter that `sync.py:_front_matter()` parses back out. Once
  `index.md` exists, that front-matter round-trip has no consumer and should go.
- **`sync.py` rsyncs whole item directories** (no include filter on the download
  pass), so `extracted/` syncs automatically — and so will paper PDFs and media.
  Watch the volume in Session 5; an exclude list may be needed.
- **Extractor output shapes and status vocabularies are inconsistent.**
  `url_output/summary.json` uses `complete|failed`; `medium_output/*/status.json`
  uses `availability: preview` and `download:`; bodies are variously `paper.md`,
  `article.md`, `post.txt`, `content.md`. Fix this **at the source** in Session 5
  — rename the outputs and unify the status vocabulary in the extractors
  themselves. Do not build the thin adapter layer the design doc's §4.1
  suggested; an adapter would only preserve inconsistency that nothing depends on.
- **`prepare_llm_input()` exists in three extractors** (`instagram/prepare.py`,
  `youtube/prepare.py`, `linkedin/prepare.py`) and already assembles the parts
  into one blob. These modules become `content.md` / `comments.md` writers.
- **`extractors/router.py:route_url()`** already dispatches to the six handlers
  and resolves shorteners. `extractors/routing_cli.py:_execute()` invokes each
  extractor's `cli.main()` with `--output-dir`; the pipeline should call the
  underlying classes (`ResearchExtractor.extract()`, `DocumentExtractor`, …)
  rather than shelling through argv.
- **Text cleaning protects segment headings** with a placeholder swap around
  `SEGMENT_HEADING_RE` (`preprocessing.py:33`) and calls
  `clean_text(resolve_links=False)`.

---

## Session 1 — Item layout and pipeline order

```
Read docs/Message-contract-design.md §3.1 and §4.1, and the "Ground rules" and
"Code facts" sections of docs/Message-contract-implementation-plan.md.

Backward compatibility is forbidden in this work. Every item currently captured
is disposable test data.

1. Restructure the item directory: message.md, source.md, telegram.json and
   attachments/ move under capture/. metadata.json stays at the item root
   (sync.py's remote metadata rsync depends on it). Update storage.py,
   preprocessing.py, sync.py, tests and docs. Do not add a fallback for the old
   layout; sync.py should reject an item that does not match the new one.
2. Reorder processing.steps in config.yaml to
   voice-transcription -> text-cleaning -> url-resolution, and confirm the
   ordering validation in config.py still accepts it.
3. Tell me the exact commands to wipe the existing test items (NAS data/inbox,
   ~/info-triage-inbox/, and the delivered-items manifest) and let me run them.

While you are in these files, fix structural problems you encounter rather than
working around them — but stay inside the item layout and pipeline; extractor
internals, the CLI surface and the dashboard are out of scope.

Then run the checks in AGENTS.md and the test suite.
```

**Notes.** The reorder is what §3.1 asks for: cleaning must strip zero-width
characters and homoglyphs before links are discovered. One consequence to verify:
`clean_url()` currently strips tracking parameters during cleaning, but resolved
`[title](url)` destinations inserted *afterwards* by `enrich_links()` will no
longer pass through the cleaner. That is fine — canonicalization moves into link
discovery in Session 2 — but note it rather than re-adding a second cleaning pass.

---

## Session 2 — Link discovery and a table-driven resolver

```
Read docs/Message-contract-design.md §3.2 and §1.3 defect 3, and the "Ground
rules" and "Code facts" sections of
docs/Message-contract-implementation-plan.md.

Add a link-discovery step that runs after text-cleaning and before
url-resolution, and produces an ordered link table:
{n, raw, canonical, from_segment, handler, priority, status}.

- Sources, in order: entities and caption_entities from each telegram.json
  payload (types url AND text_link — text_link hrefs are silently lost today,
  which is a real data-loss bug); then markdown links and bare URLs in the
  cleaned body.
- Unwrap embedded destinations (embedded_destination() exists), resolve the
  recognized shorteners including eepurl.com, canonicalize (strip utm_*, igsh,
  si, is, fbclid, rcm, cp_landing*), and deduplicate on the canonical URL.
- handler comes from extractors/router.py:route_url(); priority from
  design §3.3. Do not extract anything yet — this session only builds the table.
- The table travels on ProcessingResult as a typed field; also write links.json
  into the item for debugging.
- url-resolution becomes table-driven: it resolves titles for entries in the
  table instead of re-scanning prose. It must keep rewriting capture/message.md
  via enrich_links() so that file stays readable. resolve_budget (default 40)
  belongs in its config.yaml block.

Add tests using real telegram.json shapes, including a text_link-only item.
```

**Decisions taken during this session, settled — do not re-open.**

1. **Shortener resolution lives in `url-resolution`, not in link discovery,**
   contrary to a literal reading of §3.2. Discovery constructing its own
   `URLResolver` for `route_url()` would mean a second instance with a separate
   cache re-walking every chain that `resolve_link()` walks anyway — each
   shortener fetched twice, and a duplicate set of network knobs in
   `config.yaml`. Discovery is therefore pure and offline: `canonical` and
   `handler` are *provisional* for redirector URLs, and `url-resolution` — the
   single network stage — corrects both. **Session 5 inherits a table whose
   `handler` is already final**, so content extraction never needs to re-route.
2. **`text_link` entities are rendered inline in the segment body** as
   `[label](url)`, not merely recorded in the table. It fixes §1.3.3 at its
   source — `rendering.py` reading `text` instead of `text` + `entities` — keeps
   the author's anchor text attached to each destination (which Case 7b's `topic`
   column needs anyway), and makes §4.2's `## Captured` quote working links.
   Offsets are UTF-16 code units. Only `text_link` is rendered; `bold`, `italic`,
   `code` and `blockquote` stay ignored. Body scanning is now a genuine fallback
   rather than a load-bearing path.

---

## Session 3 — `index.md`, intent detection, and the generated inbox

```
Read docs/Message-contract-design.md §4.2, §4.3, §4.5, Appendix A, and the
"Ground rules" and "Code facts" sections of
docs/Message-contract-implementation-plan.md.

Add an index-render step (last in the pipeline) that writes <item>/index.md to
the contract in §4.2, and change sync.py to build inbox.md by concatenating
index.md files (oldest first, headings demoted one level) with the header from
§4.3 including the "N items, oldest N days" counts.

At this point there is no content extraction, so:
- extraction: none for every item, or partial when url-resolution failed;
- Sources is empty and Lead is omitted; Links carries the resolved table.

Also implement intent detection — the three heuristics in §4.5, no LLM. When the
heuristics disagree or nothing matches, leave intent empty; never invent one.
Segments stay in capture/message.md and do NOT appear in index.md.

index.md is now the only per-item contract, so remove what it replaces rather
than leaving it in place: the category front matter written by
storage.py:render_message() and parsed back by sync.py:_front_matter() has no
consumer once index.md carries the fields. Do not keep the old inbox.md
rendering path.

Update docs/DESIGN.md and AGENTS.md for the new per-item contract.
```

**Notes.** Only the fields that can be filled without extraction exist yet: `id`,
`captured_at`, `origin`, `via` (from `forward_origin` — free, currently unused),
`intent`, `kind` (`note`/`linklist`/unset), `canonical_url`, `extraction`,
`link_count`. Everything else arrives in Session 5.

---

## Session 4 — `/route` contract, and one closed loop

```
Read docs/Message-contract-design.md §6.

The org repo is at ~/Library/CloudStorage/Dropbox/notes/org/.claude/skills/
(route/SKILL.md, _shared/lenses.md). Apply exactly the three edits in §6: the
scope bullet in lenses.md, the new "The info-triage inbox" section in
route/SKILL.md, and the appended sentence under vet -> Source quality. Nothing
else in the skill changes — in particular, do not touch the finding ceiling or
add any batching or slicing rules.

Then I will capture a handful of items by hand and route them, to close the loop
once end to end. Record in docs/Message-contract-session-notes.md what the index
was missing and what /route still had to go and find.
```

**Notes.** This is the checkpoint the whole plan is ordered around — the loop has
never been closed end to end. It is a deliberate test: some captures will be
realistic, some will be contrived, and the point is to find out what `index.md`
fails to carry, not to produce a day's real routing. The system goes into real
use after Session 6.

---

## Session 5 — Content extraction

```
Read docs/Message-contract-design.md §3.3, §3.5, §4.1, §4.2, Appendix B, and the
"Ground rules" and "Code facts" sections of
docs/Message-contract-implementation-plan.md.

Add a content-extraction step that consumes the link table from Session 2 and
writes extracted/NN-<handler>-<id>/ directories.

- Unify the extractors' own output first, at the source: every extractor writes
  content.md, metadata.json and status.json with one shared status vocabulary
  (complete|partial|blocked|failed) and the existing stable reason keys. Rename
  paper.md / article.md / post.txt accordingly and delete llm_input.txt and
  llm_input.json — the three prepare.py modules become content.md writers. Do
  NOT build the thin adapter layer suggested in design §4.1; an adapter would
  only preserve inconsistency that nothing depends on. Change the retrieval, OCR
  and transcription logic itself as little as possible — that behaviour is tuned
  against real data.
- Two budgets from config.yaml: extract_budget 5 full extractions, dropping to 2
  when the item has >= 8 links (which also makes it kind: linklist);
  resolve_budget 40 title-only. Priority ranking per §3.3.
- Extraction results are cached OUTSIDE the item directory, keyed on canonical
  URL, because promote_if_current() re-materializes the item on every edit. A
  Telegram edit must not re-download a 30-page PDF.
- Per-item wall-clock cap (default 10 minutes); on exceeding it the remaining
  links downgrade to title-only and the item becomes extraction: partial.
- No extraction failure ever blocks an item.
- index.md now fills title/authors/published/venue/doi/kind, renders Sources
  with word counts, and Lead (abstract for papers, first ~120 words otherwise,
  truncated at a paragraph boundary). No LLM anywhere in this path — see §3.6.

Check what extracted/ does to sync volume; add rsync excludes for raw media if
needed. Update the docs/*_EXTRACTION.md files for the renamed outputs.
```

**Notes.** Word counts in `## Sources` are the affordance the whole design rests
on — they are what lets `/route` choose between a 200-word abstract and an
11,900-word body. Do not ship Sources without them.

---

## Session 6 — Nested extraction and the comment policy

```
Read docs/Message-contract-design.md §3.4 and Appendix B, and the "Ground rules"
section of docs/Message-contract-implementation-plan.md.

Add depth-1 recursion to content-extraction, handler-specific per the §3.4
table: plain/forwarded text harvests everything subject to the budgets; linkedin
harvests the post body plus comments authored by the post author (falling back
to the first comment containing a non-LinkedIn URL); youtube harvests
description links capped at 3, excluding social/affiliate/merch; research,
medium, document and instagram harvest nothing.

A nested extraction never harvests — depth is exactly 1 and is structural, not a
tunable counter. Each nested extraction records via: (e.g. "01 · author
comment") which appears in the index's Sources list.

The comment policy lives in each extractor's prepare module, one function:
LinkedIn and YouTube keep the author's own comments in comments.md; Instagram
comments are still retrieved and kept on disk for inspection, but never reach
content.md or the index.

kind: is promoted to what the item turned out to be — a LinkedIn post whose
author comment carries an arXiv link is kind: paper, and the Lead is the paper's
abstract, not the post's prose.
```

**Notes.** The worked example to test against is
`linkedin_output/7487448227336716288` (design §1.2 and §5 case 5) — the paper is
in the author's own first comment, the nine other comments are noise.

After this session the system is ready for real use.

---

## Session 7 (optional, and later) — the Emacs review experience

```
Read docs/Message-contract-design.md §4.4.

Design the Emacs side of reviewing the inbox, then implement it. inbox.org is
one part of that, not the whole of it: ~30 lines in sync.py generating five
lines per item (heading with date, kind and title; a PROPERTIES drawer with ID,
INTENT, URL, STATUS; file links to index.md and the item directory). Navigation
only, never content — no extracted text is embedded, which is the whole reason
index.md stays Markdown.

It carries no state: deleting the item directory remains the processed signal.
```

**Notes.** Held back deliberately so it can be designed together with the rest of
the review workflow — how items are selected, how `/route` is invoked with
background and intent, what happens after approval — rather than as an isolated
generated file. Do this once Sessions 1–6 have been used in anger.

---

## Backlog — evidence needed before building

- **OCR of Telegram photo attachments.** Instagram and YouTube Shorts OCR earn
  their cost because on-screen text is measurably where the content is; whether a
  forwarded Telegram photo carries signal or noise is untested. The engine
  already exists in `info_triage/extractors/media/ocr.py` if the answer turns out
  to be yes.
- **The bot asking "add a note?"** when an item arrives without commentary
  (session notes §6). Probably the highest-value remaining change to routing
  quality, and independent of everything above.
- Everything in design §7 is out of scope and is not planned for here.
