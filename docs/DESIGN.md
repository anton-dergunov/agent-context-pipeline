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
   ├── generated inbox.md
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

Messages are first written to a durable pending table. A module-level
`CAPTURE_GROUP_MAX_GAP_SECONDS` constant controls grouping and is currently
`3.0`. Consecutive logical messages are combined while each Telegram timestamp
gap is at most that value. A media group counts as one logical message, and a
quiet period one second longer than the configured gap prevents boundary races.
Pending captures are resumed after restart.

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
    ├── message.md
    ├── metadata.json
    ├── telegram.json
    └── attachments/
        └── 01-photo.jpg
```

The directory name is based on the Telegram creation date and message ID:

```text
YYYY-MM-DD_<message_id>
```

The date is Telegram's UTC message creation date.

The important property is that the directory name remains stable if the Telegram message is edited later.

---

# 6. `message.md`

Every ready item contains a `message.md`. It is the processed, laptop-facing
Markdown representation of the item.

For a plain text message, it may simply contain:

```markdown
This article looks useful for the ranking project:
https://example.com/article
```

The initial capture does not rewrite or summarize this content. When no
processing step applies, `message.md` therefore remains equivalent to the text
captured from Telegram. Future processing may clean or enrich it and may add
other generated files to the same item directory.

The complete original Telegram payload remains in `telegram.json`, and
downloaded source media remains in `attachments/`.

For a location or venue, it also contains a small readable location block with
the coordinates and a maps link. It remains empty for media that has neither a
caption nor a location.

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

Files associated with the item live directly inside the same directory.

For example:

```text
2026-08-08_18492/
├── message.md
├── metadata.json
├── telegram.json
└── attachments/
    ├── 01-photo.jpg
    └── 02-video.mp4
```

Different item types can produce different files.

There is no requirement for every item to have the same output structure beyond having a stable item directory and the original captured message.

The capture layer preserves useful references: text and links, forwarded source
context, documents, photos, videos, animations, voice/audio/video notes,
locations, venues, and mixed media albums. It does not archive stickers,
contacts, polls, payments, games, dice, service events, or comments. Media is
stored as supplied; transcription, OCR, and scraping are separate future
processing steps.

A media group is one logical message. It can be combined with nearby notes or
other messages under the same three-second rule. A grouped item is named from
its earliest message ID and contains ordered raw payloads, all source IDs, and
all attachments. When Telegram explicitly marks a forwarded source, that source
is rendered first and adjacent non-forwarded text appears under `## Note`.
Otherwise non-empty content is joined chronologically with blank lines.

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

The nullable SQLite `processing_step` records the currently running step. It
is retained with an error when that step fails and cleared when the item is
ready.

### Failed

Processing failed.

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

Processing steps are ordinary ordered Python functions. The initial step list
is empty. Expected future steps are text normalization, shortened-URL
resolution, OCR or transcription where applicable, and final cleanup.

The repository already contains reusable implementations for cautious text
cleanup, bounded shortened-URL resolution, Instagram extraction with tuned OCR
and transcription, and anonymous public LinkedIn extraction. These live below
`info_triage.utilities` and `info_triage.extractors`, but none is registered in
`PROCESSING_STEPS` yet. Their presence therefore does not alter the prototype's
capture or delivery behavior.

Steps write only to a revision-specific temporary workspace. `message.md` is
the processed, laptop-facing result, while `telegram.json` and original media
preserve the captured source. Storage commits generated output only if the
claimed revision remains current. A later Telegram edit therefore supersedes a
slow result without blocking capture.

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
regenerate the combined message.md
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
rendered content, and attachment specifications until finalization. The actual
ready text, HTML, images, documents, and generated outputs remain on disk.

SQLite does not need to be synchronized to the laptop.

---

# 13. Why Keep SQLite

SQLite has only a few responsibilities:

1. Durably stage the short grouping window.
2. Track the current processing state.
3. Map Telegram source messages to items and support edits.
4. Store processing errors.
5. Supply data to the web dashboard.

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
- `inbox.md` is regenerated from the item directories as a consolidated,
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
~/info-triage-inbox/inbox.md
```

This file contains one section per current item, ordered by its UTC
`received_at`. Each section contains a human-readable timestamp, a blockquoted
list of user-facing `message.md` front-matter fields, a relative link to the
item directory, and the processed Markdown body. It deliberately excludes
operational metadata such as Telegram identities, revisions, and attachment
internals.

`inbox.md` is a derived snapshot rather than acknowledgement state. It is
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

The dashboard contains one tab for each status:

```text
[ Received 3 ] [ Processing 1 ] [ Ready 12 ] [ Failed 2 ]
```

The number displayed in the tab is the number of SQLite rows currently in that state.

Selecting a tab filters the table to that status.

No separate overview dashboard is necessary.

---

# 20. Dashboard Table

Every tab displays the same simple table:

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

The initial dashboard does not need:

- charts
- statistics
- event timelines
- per-stage progress indicators
- WebSockets
- retry history
- complex item detail pages

A normal HTML table is sufficient.

---

# 21. Dashboard Refresh

The page can simply refresh automatically every few seconds, or refresh only when manually reloaded.

Real-time infrastructure is unnecessary.

---

# 22. Failure Handling

If processing fails:

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
                               ├── failure ── SQLite: failed;
                               │              remain in staging/
                               │
                               └── success ── move staging/ → inbox/;
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
- complex dashboard statistics
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
