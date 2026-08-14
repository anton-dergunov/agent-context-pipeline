# Info Triage — Simplified System Design

## 1. Purpose

Info Triage is a small self-hosted system for capturing information through Telegram, processing it on an always-running server, and making the processed items available on a laptop for manual review.

The intended workflow is:

> **Capture quickly → process automatically on the server → synchronize to laptop → process manually**

The system should remain deliberately simple.

It is not intended to become a task manager, knowledge base, read-later system, or general workflow engine. Its role is only to capture incoming information, enrich it where useful, and place it into a local inbox.

---

## 2. High-Level Architecture

```text
Telegram
   │
   ▼
Server
   │
   ├── Telegram capture
   │     sequential download and durable staging
   │
   ├── one processing worker
   │     at most one item at a time
   │
   ├── SQLite
   │     small processing-state database
   │
   ├── staging/
   │     newly received / processing / failed items
   │
   └── inbox/
         finished items
            │
            │ bidirectional synchronization
            ▼
Laptop inbox/
   ├── generated triage.md
   │     consolidated read-only view
   │
   └── self-contained item directories
            │
            ▼
      Manual processing
   │
   ▼
Item removed from laptop inbox
   │
   ▼
Deletion synchronized back to server
```

The server runs continuously.

The laptop may be offline for long periods. It synchronizes when convenient.

`app.py` is the only runtime entry point. It wires together small modules for
shared models, storage, processing, Telegram handling, and the web dashboard.
The Telegram event loop, one processing-worker thread, and one single-threaded
HTTP server are the only long-lived execution paths.

Non-secret daemon settings are loaded strictly from `config.yaml`, or from the
path named by `INFO_TRIAGE_CONFIG`. Relative paths are resolved beside that
file. `.env` contains only the bot token, authorized user ID, and optional
configuration path. Standalone extractor CLI settings and low-level container
runtime controls remain independent.

---

# 3. Capture

Telegram is the initial capture mechanism.

The user can send or share:

- plain text
- URLs
- forwarded Telegram messages
- articles
- job advertisements
- Instagram or other social-media links
- images
- videos, animations, voice notes, audio notes, and video notes
- documents
- screenshots
- locations and venues
- arbitrary notes

The Telegram bot should make capture as frictionless as possible.

The bot does not ask for classification or send a success dialog. Every new item
is assigned `Other` and continues to processing automatically. Capture failures
may still produce an error reply.

Messages are first written to a durable pending table. The
`telegram.grouping` values in `config.yaml` control the maximum gap and settling
delay. Consecutive logical messages are combined while each Telegram timestamp
gap is at most the configured maximum. A media group counts as one logical
message, and the longer quiet settling period prevents boundary races. Pending
captures are resumed after restart.

---

# 4. Item Identity

Telegram provides a `message_id`.

A Telegram message is identified by:

```text
(chat_id, message_id)
```

because `message_id` is unique within a chat rather than globally.

No additional deduplication system is required.

The same `(chat_id, message_id)` should always refer to the same captured item.
When nearby messages are grouped, the earliest message is the item identity and
every constituent identity maps to it. The chat ID remains in SQLite and
metadata, but this single-chat deployment does not need it in the human-facing
directory name.

---

# 5. Filesystem Layout

The server needs only two main directories:

```text
data/
├── staging/
└── inbox/
```

Each captured item is represented by one self-contained directory.

For example:

```text
staging/
└── 2026-08-08_18492/
    ├── index.md
    ├── metadata.json
    ├── links.json
    ├── capture/
    │   ├── source.md
    │   ├── message.md
    │   ├── telegram.json
    │   └── attachments/
    │       └── 01-photo.jpg
    └── extracted/
        └── 01-research-arxiv-2410.04840/
            ├── content.md
            ├── metadata.json
            ├── status.json
            └── raw/
                └── paper-source.pdf
```

`index.md` and `metadata.json` stay at the item root. `index.md` is the item's
own account of itself and the only file the laptop side has to read;
`metadata.json` is the item's identity, and synchronization reads every item's
revision through a metadata-only transfer. Everything captured from Telegram is
provenance and lives under `capture/`; everything retrieved from the links the
item carries lives under `extracted/`, one directory per source. Paths recorded
inside `metadata.json` are relative to the item root.

The layout is deliberately shaped like a small source tree, because that is a
shape a code-trained reader already knows how to navigate: read the contract
first, open a body when a decision needs it, never open the build output.
`capture/` and every `raw/` directory are that build output — provenance kept so
the user can inspect what was retrieved, never input for the routing side.

The directory name is based on the Telegram creation date and message ID:

```text
YYYY-MM-DD_<message_id>
```

The date is Telegram's UTC message creation date.

The important property is that the directory name remains stable if the Telegram message is edited later.

---

# 6. `capture/source.md` and `capture/message.md`

Every ready item contains both Markdown files, side by side under `capture/`.
`source.md` is the materialized text checkpoint after source-to-text work such
as voice transcription but before cleaning or URL resolution. `message.md` is the
processed body. Neither carries front matter: the item's fields belong to
`index.md`, and duplicating them would create a second source of truth. Both
files are provenance — a faithful record of what arrived and what the pipeline
made of it — and neither is what the laptop reads.

Every retained Telegram message is an explicit ordered segment. For example:

```markdown
## Segment 1 — text

This article looks useful for the ranking project:
https://example.com/article
```

The segment kind records known structure such as `text`, `caption`, `voice`, or
`location`. Explicit Telegram forwarding provenance adds `forwarded`; the
application does not infer personal commentary from text length or URLs.

The configured pipeline materializes `source.md`, cleans text, discovers the
item's links, resolves them, and then renders `message.md`. It does not
summarize the content. Each revision is reconstructed from retained Telegram
data rather than a previously processed Markdown file.

A Telegram message is plain text plus a list of entities, and the text alone
drops the destination of every hyperlinked phrase. Segments therefore render
`text_link` entities back as ordinary Markdown links, so no link is lost between
Telegram and the item.

Cleaning deliberately precedes the link work: zero-width characters and
homoglyphs can attach themselves to a URL and hide it from discovery. Discovery
itself is offline — it collects, unwraps, canonicalizes and ranks every distinct
target into `links.json` — and URL resolution is the single network stage, which
resolves that table within a per-item budget and rewrites the readable body.
Link destinations and titles inserted by resolution are therefore not cleaned
afterwards, and a second cleaning pass is not the answer.

The complete original Telegram payload remains in `capture/telegram.json`, and
downloaded source media remains in `capture/attachments/`.

For a location or venue, the segment contains a small readable location block
with coordinates and a maps link. A voice transcript is the body of its `voice`
segment.

---

# 6a. `index.md`

`index.md` is the item's contract. It is written by the last processing step and
is the only per-item file the laptop side reads: nothing else in the item has to
be opened to decide what the item is and what should happen to it.

```markdown
---
id: 2026-08-11_100
captured_at: 2026-08-11T14:33:28Z
origin: instagram
via: "@ai_machinelearning_big_data (forwarded channel)"
intent: "Interesting thought to ponder upon"
kind: post
title: "A reel worth watching"
published: 2026-08-10
canonical_url: https://www.instagram.com/reel/DbW0FoHI1OO/
extraction: ok
sources: 1
---

## Captured

> Interesting thought to ponder upon

## Sources

1. `extracted/01-instagram-DbW0FoHI1OO/` — A reel worth watching · 2026-08-10 —
   complete · `content.md` 340 words

## Lead

> [the caption, on-screen text and spoken audio, cut at 120 words] …

## Links

| # | link | handler | status |
|---|------|---------|--------|
| 1 | [A reel worth watching](https://www.instagram.com/reel/DbW0FoHI1OO/) | instagram | resolved |
```

The frontmatter is the machine-readable part and is authoritative. A field is
present only when it is actually known — an absent field is information, and an
invented one would be trusted and act on the reader. `intent` in particular is
quoted verbatim, never rewritten and never guessed: it is detected from three
positional heuristics over the segments and left empty whenever they disagree.

`## Captured` holds the user's own words and nothing else. `## Links` is the
resolved link table. Segments do not appear here at all — how many Telegram
messages carried an item is a transport detail that belongs in
`capture/message.md`.

`## Sources` lists what was retrieved, and prints each body's **word count**.
That count is the single most important affordance in the file: it turns opening
a body into a costed choice rather than a blind one. `## Lead` quotes the
top-priority source — a paper's complete abstract, otherwise the forwarded
material itself, otherwise the opening ~120 words of `content.md`, cut at a
paragraph boundary and marked with `…`.

Nothing here is summarized, and no language model runs anywhere in this path. A
truncated lead is visibly a fragment, so a reader who needs more knows to open
the body; a summary would look complete and quietly stop them. It would also
destroy exactly what source-quality judgement depends on — whether the author
shows their working — and the idiosyncratic detail that makes one artifact
different from a neighbouring one.

The file is Markdown rather than Org because extracted web text has to be
embeddable without escaping.

---

# 7. `metadata.json`

A small `metadata.json` can preserve useful source information that belongs with the exported item.

For example:

```json
{
  "chat_id": 123456,
  "message_id": 18492,
  "received_at": "2026-08-08T20:31:12+01:00",
  "edited_at": null,
  "category": "Other",
  "revision": 1
}
```

The revision starts at 1 and increases whenever any constituent Telegram
message changes.

Only metadata that may be useful outside the server should be stored here.

Operational processing state belongs in SQLite instead.

The metadata also records an attachment manifest, source message IDs, an
optional media-group ID, and any attachment-download warnings.

---

# 8. Attachments and Extracted Content

Files associated with the item live inside the same item directory.

For example:

```text
2026-08-08_18492/
├── index.md
├── metadata.json
├── links.json
├── capture/
│   ├── source.md
│   ├── message.md
│   ├── telegram.json
│   └── attachments/
│       ├── 01-photo.jpg
│       └── 02-video.mp4
└── extracted/
    ├── 01-linkedin-7492274768650407936/
    │   ├── content.md
    │   ├── comments.md
    │   ├── metadata.json
    │   ├── status.json
    │   └── raw/
    └── 02-research-arxiv-2607.12345/
        ├── content.md
        ├── metadata.json
        ├── status.json
        └── raw/
```

Every extraction directory has the same four names whichever of the six handlers
produced it, so a reader learns one convention rather than six. `content.md` is
always the body; `status.json` always reports `complete`, `partial`, `blocked` or
`failed` with a stable reason. Different item types can still produce different
files under `raw/`.

There is no requirement for every item to have the same output structure beyond having a stable item directory and the original captured message.

The capture layer preserves useful references: text and links, forwarded source
context, documents, photos, videos, animations, voice/audio/video notes,
locations, venues, and mixed media albums. It does not archive stickers,
contacts, polls, payments, games, dice, service events, or comments. Media is
stored as supplied. Voice notes are transcribed automatically; OCR, scraping,
and transcription of other media remain future processing steps. See
[`PREPROCESSING.md`](PREPROCESSING.md) for the authoritative behaviour matrix.

A media group is one logical message. It can be combined with nearby notes or
other messages under the same three-second rule. A grouped item is named from
its earliest message ID and contains ordered raw payloads, all source IDs, and
all attachments. Source messages remain chronological and appear as separate
segments. Explicit forwards are labeled without reordering or guessing which
other segment contains the user's intent.

---

# 9. Processing Lifecycle

There are four logical states:

```text
received
processing
ready
failed
```

The filesystem and SQLite work together.

### Received

The Telegram message has been saved into:

```text
staging/<item>/
```

SQLite contains:

```text
status = received
```

### Processing

A worker is currently processing the item.

The item remains in:

```text
staging/<item>/
```

SQLite contains:

```text
status = processing
```

No `.processing` marker file is required.

The nullable SQLite `processing_step` records the currently running step. It is
cleared when the item is ready. Declared processor problems are tracked by
processor telemetry and do not put the item in the global failed state.

### Failed

Capture, orchestration, storage, commit, or unexpected processor code failed.

The item remains in:

```text
staging/<item>/
```

SQLite contains:

```text
status = failed
```

The error message can also be stored in SQLite.

No `.failed` marker file is required.

### Ready

Processing completed successfully.

The entire item directory is moved:

```text
staging/<item>/
        ↓
inbox/<item>/
```

SQLite contains:

```text
status = ready
```

The move into `inbox/` is the filesystem representation that the item is ready for synchronization.

If no implemented processing step applies, an item moves directly from
`received` to `ready` without occupying the worker.

---

# 10. Processing

Processing depends on the type of captured information.

A plain Telegram note may require almost no processing.

One background worker claims the oldest `received` row and processes only one
item at a time. SQLite is the durable queue; there is no separate queue table or
in-memory-only job list. On restart, an interrupted `processing` item still in
staging returns to `received`.

Processing steps are ordinary ordered Python functions registered from the
strict, commented `config.yaml`. The shipped order transcribes Telegram voice
attachments, cleans text, discovers the item's links, resolves them, extracts the
content behind the highest-priority ones, and renders `index.md` last. Voice
output is materialized in `capture/source.md` before the text transforms.
Expected future steps include OCR of photo attachments, depth-1 nested
extraction, and item-level classification.

Content extraction is bounded on both axes: a link budget (five full
extractions, dropping to two when the item is a link list) and a per-item
wall-clock ceiling, after which the remaining links stay title-only. It is the
only expensive stage, so its results are cached outside the item directory and
keyed on the canonical URL — an item is rebuilt from `capture/telegram.json` on
every Telegram edit, and adding a note to a message must not re-download the
paper attached to it. No extraction failure ever blocks an item: the failure is
recorded against its link and the item lands with everything else it has.

The repository already contains reusable implementations for cautious text
cleanup, bounded shortened-URL resolution, Instagram extraction with tuned OCR
and transcription, and anonymous public LinkedIn extraction. These live below
`info_triage.utilities` and `info_triage.extractors`. The Telegram pipeline
reuses the local transcription engine, URL resolver, and text cleaner; their
standalone commands remain available.

Steps write only to a revision-specific temporary workspace. Storage commits
`capture/source.md`, `capture/message.md`, and generated output only if the
claimed revision remains current. Generated output is item-root-relative and may
not land inside `capture/`, which belongs to the capture layer alone.
`capture/telegram.json` and original media preserve the exact captured source. A later Telegram edit therefore supersedes a slow result without
blocking capture.

Each step has its own nested workspace and a snapshot of the accumulated
result. A step can succeed, complete partially with one or more recoverable
issues, or declare failure. Partial output is retained. Declared failed output
and generated files are discarded before the next step runs. Unexpected Python
exceptions are treated as code failures and retain the existing item-level
failure behavior.

The worker records every actual step execution, including retries and later
revisions. It appends one compact JSON event per physical line to
`data/logs/processor-runs.jsonl`. Success events contain status, duration,
processor, and item identity only. Partial and failed events also contain stable
reason keys, full untruncated input Markdown, failed targets, and exception
details when applicable. Transformed results, raw Telegram JSON, and binary
media are never logged. The log is append-only and currently has no rotation or
cleanup.

A URL may require:

```text
retrieve page
    ↓
extract useful content
    ↓
optionally classify / summarize
    ↓
save generated files into item directory
```

An Instagram post may require additional extraction of:

- caption
- images
- post metadata
- useful information contained in the images

A job advertisement may later have its own extraction pipeline.

The system does not need a general workflow engine. Processing can simply be ordinary Python code that handles different source types.

---

# 11. Telegram Message Edits

Telegram edits should be supported.

If an already captured Telegram message is edited, its `(chat_id, message_id)`
is resolved to the containing item. This may be the edited message itself or the
earliest message in a grouped capture.

The system then:

```text
receive edited message
    ↓
replace that member's raw payload, content, and attachments
    ↓
regenerate the segmented capture/source.md
    ↓
run the configured processors, regenerate capture/message.md and index.md
    ↓
increment the metadata revision
    ↓
if item is already in inbox:
    move it back to staging
    ↓
status = received
    ↓
process again
    ↓
move back to inbox when finished
```

Unedited constituent messages and their attachments remain in the item. This
ensures that edited source information is reprocessed without breaking the
grouped capture.

The stable item directory name is important here.

---

# 12. SQLite

SQLite is intentionally small. It stores processing state, source-to-item
identity mappings, and the short-lived durable input needed during the grouping
quiet period. Pending raw payloads are deleted immediately after a batch is
successfully written to its item directory; ready content is not kept in the
database.

A minimal table is sufficient:

```sql
CREATE TABLE items (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,

    status TEXT NOT NULL,

    category TEXT,
    revision INTEGER NOT NULL DEFAULT 1,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    short_text TEXT,
    error TEXT,
    processing_step TEXT,

    PRIMARY KEY (chat_id, message_id)
);
```

Possible values of `status` are:

```text
received
processing
ready
failed
```

`item_messages` maps every constituent `(chat_id, message_id)` to the primary
item. `pending_capture_messages` temporarily holds each message's raw payload,
rendered content, and attachment specifications until finalization.
`processor_stats` stores lifetime succeeded, partial, and failed run counts;
`processor_reason_stats` stores stable reason occurrence counts. Full event
details remain only in JSONL. The actual ready text, HTML, images, documents,
and generated outputs remain on disk.

SQLite does not need to be synchronized to the laptop.

---

# 13. Why Keep SQLite

SQLite has only a few responsibilities:

1. Durably stage the short grouping window.
2. Track the current processing state.
3. Map Telegram source messages to items and support edits.
4. Store processing errors.
5. Store cumulative processor outcome and reason counters.
6. Supply data to the web dashboard.

It is not:

- a content database
- a permanent history
- an export database
- an acknowledgement system
- a distributed queue

This keeps SQLite useful without making it central to the whole architecture.

---

# 14. SQLite Pruning

SQLite is operational rather than archival.

Old rows can therefore be deleted automatically.

A simple policy is:

> Remove rows whose `updated_at` is older than 30 days.

The filesystem remains the source of actual content.

The pruning process can run periodically, for example once per day.

There is no need to preserve indefinite processing history.

---

# 15. Server-to-Laptop Synchronization

The server `inbox/` and laptop `inbox/` should behave like two synchronized copies of the same working inbox.

Conceptually:

```text
server/inbox/  ⇄  laptop/inbox/
```

The roles are intentionally asymmetric:

```text
SERVER
creates new item directories

LAPTOP
reads, processes, moves or deletes item directories
```

After synchronization:

- new server items appear on the laptop;
- items removed from the laptop are removed from the server.
- an item with a revision newer than the last laptop revision appears again,
  even if the older local revision was removed.
- `triage.md` is regenerated from the item directories as a consolidated,
  oldest-first processing view.

There is no separate archive or acknowledgement protocol.

---

# 16. Manual Processing on the Laptop

The laptop receives ordinary self-contained directories.

For example:

```text
~/InfoTriage/inbox/
├── 2026-08-08_18492/
├── 2026-08-08_18493/
└── 2026-08-08_18494/
```

The user processes these items one by one.

The synchronization command also generates:

```text
~/info-triage-inbox/triage.md
```

`triage.md` is the items' `index.md` files concatenated, oldest first by UTC
`received_at`, under one `## <id>` heading each and with their own headings
demoted one level. Because it is a concatenation there is no drift and no second
source of truth: whatever an item claims about itself, it claims identically in
both places. Each item's frontmatter is fenced as a YAML block, since frontmatter
is only unambiguous at the top of a file.

A short generated header gives the two operational numbers — how many items are
waiting and how old the oldest is — and tells the reader which files are
provenance and how to file an item. Operational metadata such as Telegram
identities, revisions, and attachment internals never appears.

`triage.md` is a derived snapshot rather than acknowledgement state. It is
replaced atomically after every successful sync, so edits to it are not
preserved. The linked item directories remain the authoritative inbox.

Once an item has been dealt with, it is moved elsewhere in the user's own system or removed from the Info Triage inbox.

Info Triage does not need to know where it goes afterwards.

That may be:

- an Org file
- a project directory
- a task system
- a notes system
- a job-processing pipeline
- a read-later collection
- somewhere else entirely

From Info Triage's perspective, removing it from the synchronized inbox means it has been consumed.

---

# 17. Synchronization Tool

A normal bidirectional synchronization tool is preferable to treating raw `rsync` as a two-way synchronization protocol.

The desired behavior is:

```text
server additions → laptop
laptop deletions → server
```

Possible tools include:

- Unison
- Syncthing
- another simple bidirectional file synchronizer

The implementation uses a small Python synchronization command, launched by
`sync.sh`, to orchestrate `ssh` and `rsync`. A local manifest maps each item ID
to its last delivered revision. This is the minimum state needed to distinguish
an unchanged processed item from a newer server update. Python keeps the state
transitions and generated Markdown parsing directly testable while `sync.sh`
remains the stable user-facing command.

The desired laptop experience should be approximately:

```bash
info-triage sync
```

or a direct invocation of the chosen synchronization tool.

No additional pull/acknowledge commands are required.

---

# 18. Web Dashboard

The server exposes a very small web dashboard.

Its purpose is only to show the state of Telegram captures and processing.

It is not intended to become another interface for manually processing captured information.

The dashboard reads its data directly from SQLite.

---

# 19. Dashboard Tabs

The dashboard contains one tab for each item status plus processor telemetry:

```text
[ Received 3 ] [ Processing 1 ] [ Ready 12 ] [ Failed 2 ] [ Processors ]
```

The number displayed in the tab is the number of SQLite rows currently in that state.

Selecting an item tab filters the table to that status. The Processors tab
shows every configured or historically observed processor with lifetime runs,
succeeded, partial, and failed counts. Its reason rows show outcome, occurrence
count, and a `processor:reason` key that can be copied into `grep -F` against
`data/logs/processor-runs.jsonl`.

No separate overview dashboard is necessary.

---

# 20. Dashboard Tables

Item-status tabs display the same simple table:

```text
ID              Created              Updated              Message
---------------------------------------------------------------------------
2026-08-08_18492  2026-08-08 20:31   2026-08-08 20:31     Interesting article...
2026-08-08_18488  2026-08-08 20:25   2026-08-08 20:27     https://instagram...
```

The columns are:

- **ID** — the `YYYY-MM-DD_<message_id>` item directory name
- **Created** — when the Telegram message was first received
- **Updated** — last relevant update
- **Message** — short preview of the Telegram text, plus the current or failed
  processing step when present

For failed items, the error can also be displayed, either as another column or below the short text.

The dashboard does not need:

- charts
- event timelines
- per-stage progress indicators
- WebSockets
- retry history
- complex item detail pages

A normal HTML table is sufficient.

The Processors tab uses another plain table with columns for processor, runs,
succeeded, partial, failed, and the stable reason breakdown. It does not expose
full inputs or tracebacks in the browser; those remain in the JSONL log.

---

# 21. Dashboard Refresh

The page can simply refresh automatically every few seconds, or refresh only when manually reloaded.

Real-time infrastructure is unnecessary.

---

# 22. Failure Handling

If infrastructure, commit logic, or unexpected processor code fails:

```text
status = failed
```

and the item remains in `staging/`.

The error is stored in SQLite.

A simple retry mechanism can later reset:

```text
failed → received
```

and let the normal processing loop try again.

Retrying can initially be done through a command-line command or a simple dashboard button.

No dedicated failure directory is required.

Expected processor problems do not use this item state. A partial processor
keeps its usable output; a declared failed processor rolls back its own changes;
then the pipeline advances and delivers the item. Both are counted and logged
on the Processors tab.

---

# 23. Simplified Directory and State Model

The complete server-side model is:

```text
data/
├── staging/
│   ├── item A     received
│   ├── item B     processing
│   └── item C     failed
│
└── inbox/
    ├── item D     ready
    └── item E     ready
```

SQLite tells us which state each `staging/` item is in.

Anything in `inbox/` is ready.

---

# 24. End-to-End Workflow

```text
Telegram message(s)
      │
      ▼
durably wait for the three-second grouping window
      │
      ▼
save grouped item into staging/
      │
      ▼
SQLite: received
      │
      ▼
check applicable steps
      │
      ├── none ── move staging/ → inbox/; SQLite: ready
      │
      └── one or more ── single worker; SQLite: processing
                               │
                               ├── declared step issue ── log/count;
                               │   retain partial output or roll back failed step;
                               │   continue remaining steps
                               │
                               ├── unexpected/infrastructure failure ── SQLite: failed;
                               │   remain in staging/
                               │
                               └── pipeline complete ── move staging/ → inbox/;
                                   SQLite: ready
      │
      ▼
synchronize
      │
      ▼
item appears in laptop inbox
      │
      ▼
manual processing
      │
      ▼
remove/move item from laptop inbox
      │
      ▼
synchronize
      │
      ▼
item removed from server inbox
```

For an edited Telegram message:

```text
edited Telegram message
      │
      ▼
resolve (chat_id, message_id) to its grouped item
      │
      ▼
move inbox item back to staging if necessary
      │
      ▼
replace that source and regenerate grouped content
      │
      ▼
SQLite: received
      │
      ▼
normal processing again
```

---

# 25. What the System Deliberately Does Not Have

The simplified design does not require:

- PostgreSQL
- Redis
- Celery
- Kafka
- object storage
- distributed queues
- explicit export state
- explicit acknowledgement state
- archive directories
- server-side retention of consumed content
- filesystem marker files
- an item-events table
- processing timelines
- a frontend SPA
- bidirectional application-level APIs for synchronization

The main components are simply:

```text
Telegram bot
Python server
SQLite
filesystem
processing code
simple web page
bidirectional folder synchronization
```

---

# 26. Design Principles

## Keep capture trivial

The normal interaction should remain:

```text
Share → Telegram → done
```

## Keep content in files

Files are easy to inspect, copy, process, search, and use with arbitrary tools.

## Keep state in SQLite

SQLite only records small operational facts that are awkward to infer from files.

## Keep the state machine tiny

Only:

```text
received
processing
ready
failed
```

## Keep only two server directories

```text
staging/
inbox/
```

## Treat one item as one directory

Everything belonging to a capture travels together.

## Let the laptop consume the inbox

The server produces items.

The laptop decides when they have been dealt with.

## Synchronize rather than build a delivery protocol

There is no need for export acknowledgements or retention logic.

## Avoid permanent history

SQLite can be pruned after roughly one month.

## Keep the dashboard observational

Its job is to answer:

- What has just arrived?
- What is processing?
- What is ready?
- What failed?

Nothing more is required initially.

## Add complexity only when a real problem appears

The design should remain a small personal tool rather than evolving pre-emptively into a general distributed system.
