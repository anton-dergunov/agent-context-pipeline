# Info Triage — System Design

## 1. Overview

Info Triage is a self-hosted personal information capture, processing, and routing system.

Its purpose is to provide a very low-friction way to capture information encountered on different devices, automatically retrieve and process that information on an always-running server, and later deliver the processed results to a laptop for manual review.

The central principle is:

> **Capture immediately. Understand automatically. Decide manually later.**

The system is not intended to replace a task manager, knowledge base, read-later application, or personal notes system. Instead, it acts as an **ingestion and triage layer** in front of those systems.

The initial capture interface is Telegram.

The processing service runs continuously on a dedicated server. The laptop does not need to be online while information is being captured or processed.

Processed items accumulate safely on the server until the laptop retrieves them.

---

# 2. High-Level Architecture

```text
                           SERVER
                    ┌──────────────────┐
                    │                  │
Telegram ──────────►│  Capture Service │
                    │                  │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │      SQLite      │
                    │                  │
                    │ Processing state │
                    │ Queue / history  │
                    │ Retry state      │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Processing       │
                    │ Pipeline         │
                    │                  │
                    │ Fetch            │
                    │ Extract          │
                    │ Classify         │
                    │ Enrich           │
                    │ Export           │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Server           │
                    │ Filesystem       │
                    │                  │
                    │ raw/             │
                    │ processed/       │
                    │ export/          │
                    └────────┬─────────┘
                             │
                             │
                  ┌──────────┴──────────┐
                  │                     │
                  ▼                     ▼
          ┌───────────────┐      ┌───────────────┐
          │ Web Dashboard │      │ Laptop Pull   │
          │               │      │ API / SSH     │
          │ Queue         │      └───────┬───────┘
          │ Processing    │              │
          │ History       │              │ rsync
          │ Errors        │              │
          └───────────────┘              ▼
                                ┌──────────────────┐
                                │     LAPTOP       │
                                │                  │
                                │ InfoTriage/      │
                                │   Inbox/         │
                                └────────┬─────────┘
                                         │
                                         ▼
                                  Manual processing
                                         │
                                         ▼
                                    Acknowledge
                                         │
                                         ▼
                                Server marks item
                                  ACKNOWLEDGED
```

---

# 3. Capture Interface

## 3.1 Telegram

Telegram is initially the primary capture mechanism.

The user can send content to a dedicated Telegram bot from a phone, tablet, laptop, or any other device with Telegram installed.

Supported inputs may include:

- URLs
- plain text
- forwarded messages
- images
- documents
- screenshots
- messages containing both text and links
- social-media links
- job advertisements
- emails copied or forwarded into Telegram

Telegram therefore acts as a universal cross-device "share to Info Triage" interface.

For example:

```text
Web browser
    │
    │ Share
    ▼
Telegram
    │
    ▼
Info Triage bot
```

The same workflow works for applications such as Instagram where a post can be shared into Telegram.

---

## 3.2 Explicit Classification

The Telegram bot may optionally allow manual classification.

For example, after receiving an item it could offer buttons such as:

```text
[ Job ] [ Read Later ] [ Reference ]

[ Task ] [ Idea ] [ Auto ]
```

Manual classification is optional.

The normal low-friction workflow should remain:

```text
Share → Info Triage
```

with automatic classification performed by the server.

If the user explicitly specifies a category, that information should take precedence over, or at least be recorded alongside, automatic classification.

---

# 4. Example Content Types

The system should support heterogeneous inputs.

## Job advertisements

A job posting may later be processed into structured information such as:

- company
- role
- location
- compensation
- responsibilities
- required skills
- technologies
- seniority
- remote/hybrid requirements
- other useful attributes

## Articles

An article may represent:

- read later
- already read but worth keeping
- reference material
- something requiring action
- something worth summarizing

## Social-media posts

For posts such as Instagram content, the system may retrieve and retain:

- description
- text
- author
- URL
- images
- useful metadata
- extracted information from images

## Email

Interesting emails can eventually be submitted to the same pipeline.

## Notes and text snippets

Telegram can also be used simply as a capture box:

```text
Investigate whether X would work for project Y
```

The server can classify this as an idea, task, reference, or other appropriate category.

---

# 5. Server-Side Storage

The system deliberately separates two kinds of storage:

1. **content storage**
2. **operational state**

These should not be conflated.

---

# 6. Filesystem — Content Storage

Actual captured and generated content is stored on the filesystem.

This includes:

- HTML
- extracted text
- Markdown
- JSON
- images
- PDFs
- screenshots
- Telegram attachments
- downloaded files
- generated summaries
- source metadata

Large content should generally **not** be stored as SQLite BLOBs.

An example layout:

```text
/var/lib/info-triage/
├── db/
│   └── triage.sqlite
│
├── raw/
│   ├── 01K2ABC.../
│   └── 01K2DEF.../
│
├── processed/
│   ├── 01K2ABC.../
│   └── 01K2DEF.../
│
├── export/
│   ├── 01K2ABC.../
│   └── 01K2DEF.../
│
└── failed/
```

Each item receives a unique internal ID.

A UUID or ULID could be used.

ULIDs have the useful property of being naturally sortable by creation time.

---

# 7. SQLite — Operational State

SQLite is **not** the primary content store.

It acts as the server's:

- processing ledger
- persistent queue
- state database
- retry tracker
- deduplication mechanism
- audit/history source

This database remains on the server.

It is **not synchronized to the laptop**.

Only exported content is transferred to the laptop.

---

# 8. Why SQLite Is Useful

Without a state database, application state would have to be inferred from filesystem structure.

For example:

```text
incoming/
processing/
ready/
failed/
```

This can work for a very small implementation, but it becomes increasingly difficult to answer operational questions.

SQLite makes it easy to determine:

- Which Telegram messages have already been received?
- Which messages have not yet been processed?
- Which items are currently being processed?
- Which items are waiting in the queue?
- Which items failed?
- Why did an item fail?
- How many times has processing been attempted?
- Which items are ready for the laptop?
- Which items have already been downloaded?
- Which items have been manually processed?
- When was an item last updated?
- How long did processing take?

The web dashboard can obtain this information from exactly the same database.

---

# 9. Initial Database Schema

A first implementation may require only one main table.

For example:

```sql
CREATE TABLE items (
    id TEXT PRIMARY KEY,

    telegram_chat_id INTEGER,
    telegram_message_id INTEGER,

    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    source_type TEXT,
    source_url TEXT,

    requested_category TEXT,
    detected_category TEXT,

    status TEXT NOT NULL,

    raw_path TEXT,
    processed_path TEXT,
    export_path TEXT,

    processing_attempts INTEGER DEFAULT 0,

    error TEXT,

    processing_started_at TEXT,
    processing_finished_at TEXT,

    exported_at TEXT,
    acknowledged_at TEXT,

    UNIQUE(telegram_chat_id, telegram_message_id)
);
```

The exact schema can evolve as the application develops.

---

# 10. Telegram Deduplication

Telegram may retry delivery under some circumstances.

Processing the same capture twice would be undesirable.

The constraint:

```sql
UNIQUE(telegram_chat_id, telegram_message_id)
```

makes Telegram ingestion idempotent.

If the same message is delivered twice, the server can recognize that it has already been accepted.

---

# 11. Item Lifecycle

A captured item moves through explicit states.

A possible initial state machine is:

```text
RECEIVED
    │
    ▼
QUEUED
    │
    ▼
FETCHING
    │
    ▼
FETCHED
    │
    ▼
PROCESSING
    │
    ▼
READY
    │
    ▼
EXPORTED
    │
    ▼
ACKNOWLEDGED
```

Failures can occur during processing:

```text
FETCHING ──────► FAILED
PROCESSING ────► FAILED
EXPORTING ─────► FAILED
```

Failed operations can subsequently be retried.

The exact number of states should remain small unless finer-grained states provide actual operational value.

---

# 12. Crash Recovery

Persistent state makes processing resilient to server restarts.

Suppose an item reaches:

```text
FETCHED
```

and the server process crashes before LLM processing begins.

When the application restarts, it can query SQLite for unfinished items:

```sql
SELECT *
FROM items
WHERE status NOT IN ('ACKNOWLEDGED', 'FAILED');
```

The worker can then determine which processing stages still need to run.

This avoids depending entirely on in-memory queues.

---

# 13. Processing Pipeline

Conceptually:

```text
Capture
   │
   ▼
Retrieve
   │
   ▼
Extract
   │
   ▼
Classify
   │
   ▼
Enrich
   │
   ▼
Export
```

Different source types may use different processing pipelines.

For example:

```text
Instagram URL
      │
      ├── retrieve post
      ├── extract caption
      ├── download images
      ├── analyse images
      ├── classify
      ├── summarize
      └── export
```

while:

```text
plain Telegram text
      │
      ├── classify
      ├── extract useful information
      └── export
```

---

# 14. Export Format

Once processing is complete, the system creates a self-contained export package.

For example:

```text
export/
└── 01K2ABCDEF/
    ├── item.md
    ├── metadata.json
    ├── original.html
    ├── image-01.jpg
    └── image-02.jpg
```

Not every item needs every file.

A simple Telegram note might contain only:

```text
01K2XYZ/
├── item.md
└── metadata.json
```

---

# 15. Human-Readable Representation

`item.md` is the primary human-readable representation.

For example:

```markdown
---
id: 01K2ABCDEF
source: instagram
category: reference
captured: 2026-08-08T13:42:11+01:00
url: https://...
---

# Post title / generated title

## Summary

...

## Extracted information

...

## Original text

...
```

The precise format can evolve independently of the internal database.

`metadata.json` can contain more detailed machine-readable information.

---

# 16. Laptop Delivery

The laptop is not expected to run continuously.

Therefore the server should **not depend on being able to connect to the laptop**.

The laptop initiates synchronization.

Conceptually:

```text
SERVER                    LAPTOP

export/
   │
   │      laptop pull
   ├─────────────────────► Inbox/
   │
```

SSH + `rsync` is a suitable initial transport.

---

# 17. Why Pull Instead of Push

The server runs continuously.

The laptop may be:

- asleep
- disconnected
- travelling
- behind NAT
- on a different network

Therefore pushing from server to laptop introduces unnecessary connectivity problems.

With pull:

```text
Laptop becomes available
        │
        ▼
Laptop asks server for new items
        │
        ▼
Items are transferred
```

The server simply retains completed items until they are retrieved.

---

# 18. Synchronization vs Transfer

The laptop and server directories should not necessarily be treated as two mirrors of the same filesystem.

The desired semantics are closer to a reliable delivery queue.

Therefore a blind bidirectional synchronization mechanism is not ideal.

In particular, automatically propagating laptop deletions back to the server could be dangerous.

If the laptop directory were accidentally removed, a synchronization command using deletion propagation could potentially remove the server copy as well.

Instead, delivery and deletion should be separate operations.

---

# 19. Laptop Pull Command

Eventually the project should provide a simple command such as:

```bash
info-triage pull
```

rather than requiring the user to remember raw `rsync` commands.

Conceptually the command performs:

```text
1. Determine which server items are READY.

2. Download those items.

3. Verify successful local creation.

4. Tell the server which items arrived successfully.

5. Server changes those items from READY to EXPORTED.
```

The underlying transfer mechanism can still simply be SSH + `rsync`.

---

# 20. Manual Processing

Downloaded items arrive in a local inbox such as:

```text
~/InfoTriage/
├── Inbox/
├── Processed/
└── Archive/
```

or potentially categorized:

```text
~/InfoTriage/Inbox/
├── jobs/
├── read-later/
├── references/
├── tasks/
├── ideas/
└── unknown/
```

The laptop-side representation is intentionally based on ordinary files.

This allows the information to be processed with arbitrary tools:

- Emacs
- Org mode
- VS Code
- shell scripts
- Python
- ripgrep
- Git
- LLM tools
- future custom tooling

The server does not dictate the final destination of an item.

---

# 21. Acknowledgement

Downloading an item and processing an item are different events.

Successful transfer means:

```text
READY → EXPORTED
```

It does **not** mean the item has been dealt with.

After manual processing on the laptop, the user can explicitly acknowledge the item.

For example:

```bash
info-triage done 01K2ABCDEF
```

The laptop sends an acknowledgement to the server.

The server records:

```text
EXPORTED → ACKNOWLEDGED
```

and:

```text
acknowledged_at = ...
```

This creates explicit delivery semantics rather than using filesystem deletion as an implicit signal.

---

# 22. Server Retention

Acknowledgement does not necessarily have to immediately delete the original data.

A useful policy could be:

```text
ACKNOWLEDGED
      │
      │ retain 30 days
      ▼
DELETE / ARCHIVE
```

This provides protection against accidental loss.

Retention policy should be configurable.

---

# 23. Web Dashboard

The server should provide a lightweight browser-based dashboard.

The dashboard is primarily an **operational view of the processing pipeline**, not a replacement for manual laptop processing.

Its purpose is to answer questions such as:

> Did the thing I just sent through Telegram arrive?

> Is it being processed?

> How large is the queue?

> What has been processed recently?

> Did anything fail?

> Has this item already been downloaded to my laptop?

SQLite naturally provides the data required for this interface.

---

# 24. Dashboard Overview

The main page could contain a compact status summary:

```text
Info Triage

Queue
──────────────
Waiting             3
Processing          1
Ready               7
Failed              1

Today
──────────────
Received           18
Processed          15
Exported           12
Acknowledged        9
```

Below this can be a live/recent item list.

---

# 25. Currently Processing

A dedicated section should show active work:

```text
CURRENTLY PROCESSING

14:46  Instagram
       "Interesting post about..."
       PROCESSING
       Running image extraction
       00:18

14:47  Article
       "Scaling recommendation..."
       FETCHING
       00:04
```

This provides immediate visibility into whether the worker is functioning.

---

# 26. Queue

The queue view shows items waiting for processing:

```text
QUEUE

#   Received   Type       Description
────────────────────────────────────────────────
1   14:47      Article    example.com/...
2   14:48      Instagram  instagram.com/...
3   14:48      Text       "Look into..."
```

Queue ordering should normally correspond to processing order.

---

# 27. Recently Processed

Another section should show recent activity:

```text
RECENT

14:43  Job        READY
14:41  Instagram  READY
14:37  Article    EXPORTED
14:32  Text       ACKNOWLEDGED
14:25  Article    FAILED
```

This provides a quick operational history without requiring SSH access or database queries.

---

# 28. Item Details

Clicking an item should open a detail page.

For example:

```text
Item: 01K2ABCDEF

Status
    READY

Source
    Telegram / Instagram

Received
    2026-08-08 14:41:13

Processing started
    2026-08-08 14:41:15

Processing finished
    2026-08-08 14:41:37

Duration
    22 seconds

Category
    reference

Source URL
    https://instagram.com/...

Files
    item.md
    metadata.json
    image-01.jpg
    image-02.jpg

Processing
    ✓ Telegram capture
    ✓ Source retrieval
    ✓ Text extraction
    ✓ Image download
    ✓ Image analysis
    ✓ Classification
    ✓ Summary
    ✓ Export
```

Errors should also be visible here.

---

# 29. Failed Items

Failures should be highly visible.

For example:

```text
FAILED

14:25  Instagram

Could not retrieve Instagram post.

Attempt: 3
Last attempt: 14:31
```

The dashboard could eventually provide:

```text
[ Retry ]
```

This is useful because some failures may be transient.

---

# 30. Processing Progress

A single `status` column provides coarse progress.

Eventually, more detailed progress may be useful.

For example:

```text
PROCESSING
    └── current_stage = "image_analysis"
```

Possible stages:

```text
capture
fetch
extract
download_assets
classify
image_analysis
summarize
export
```

This can be represented separately from the overall item lifecycle.

For example:

```text
status = PROCESSING
stage  = IMAGE_ANALYSIS
```

The dashboard can therefore display:

```text
Processing — analysing 4 images
```

rather than merely:

```text
PROCESSING
```

---

# 31. Processing Events

A later version may introduce an `item_events` table.

For example:

```sql
CREATE TABLE item_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT
);
```

This could record:

```text
14:41:13 RECEIVED
14:41:14 QUEUED
14:41:15 FETCH_STARTED
14:41:18 FETCH_FINISHED
14:41:19 EXTRACTION_STARTED
14:41:21 IMAGE_DOWNLOAD
14:41:23 LLM_STARTED
14:41:36 LLM_FINISHED
14:41:37 READY
```

The dashboard could then show a detailed processing timeline.

This table is optional for the initial implementation but would make debugging and observability considerably better.

---

# 32. Dashboard Architecture

The dashboard does not need a complex frontend initially.

A simple server-rendered web application is sufficient.

Conceptually:

```text
Browser
   │
   ▼
HTTP server
   │
   ▼
SQLite
```

The dashboard primarily performs read-only SQL queries.

For example:

```sql
SELECT *
FROM items
WHERE status = 'PROCESSING'
ORDER BY processing_started_at;
```

or:

```sql
SELECT *
FROM items
ORDER BY received_at DESC
LIMIT 50;
```

This keeps the first implementation extremely small.

---

# 33. Dashboard Refresh

Initially, the dashboard can simply refresh periodically.

For example, every few seconds.

There is no need to introduce WebSockets or another real-time messaging system just to display processing progress.

If the application later requires genuinely live updates, Server-Sent Events or WebSockets can be introduced.

---

# 34. Dashboard Actions

The initial dashboard should primarily be observational.

Useful later actions include:

```text
[ Retry ]

[ Reprocess ]

[ Change category ]

[ Delete ]

[ Download ]

[ Mark acknowledged ]
```

Administrative actions should be deliberately limited so that the dashboard does not gradually become a second manual-processing application.

The laptop remains the primary place where captured information is actually reviewed and acted upon.

---

# 35. Security

The dashboard exposes personal captured information and therefore must not simply be exposed anonymously to the public Internet.

Possible approaches include:

- authentication
- VPN access
- private network access
- reverse proxy authentication
- access through a service such as Tailscale

The first implementation can remain private and simple rather than building a complete user/account system.

---

# 36. Operational Separation

The system should maintain a clear separation between:

### Capture

Telegram provides fast cross-device capture.

### Processing

The always-running server retrieves, extracts, classifies, enriches, and packages content.

### Operational state

SQLite records what has happened and what needs to happen next.

### Content storage

The filesystem contains actual source material and generated artifacts.

### Monitoring

The web dashboard shows processing activity, queue state, history, and failures.

### Delivery

The laptop explicitly pulls completed packages from the server.

### Decision

The user manually reviews and processes the resulting information on the laptop.

---

# 37. Complete Item Lifecycle

The resulting end-to-end workflow is:

```text
             PHONE / OTHER DEVICE

                    Telegram
                       │
                       ▼
              ┌─────────────────┐
              │     SERVER      │
              │                 │
              │ Receive         │
              │      ↓          │
              │ Queue           │
              │      ↓          │
              │ Fetch           │
              │      ↓          │
              │ Extract         │
              │      ↓          │
              │ Classify        │
              │      ↓          │
              │ Enrich          │
              │      ↓          │
              │ Export          │
              │                 │
              └────────┬────────┘
                       │
                       │
              READY on server
                       │
                       │
        ┌──────────────┴───────────────┐
        │                              │
        ▼                              ▼
 Web dashboard                   Laptop available
 shows READY                          │
                                     │
                                     ▼
                              info-triage pull
                                     │
                                     ▼
                              Local Inbox/
                                     │
                                     ▼
                              Manual processing
                                     │
                                     ▼
                              info-triage done
                                     │
                                     ▼
                                ACKNOWLEDGED
                                     │
                                     ▼
                              retention period
                                     │
                                     ▼
                              archive / delete
```

---

# 38. Initial Implementation Scope

A sensible first version does not need much infrastructure.

### Server

- Telegram bot
- Python application
- SQLite
- ordinary filesystem storage
- processing worker
- simple HTTP dashboard
- SSH access

### Laptop

- SSH
- `rsync`
- small `info-triage` command/script
- ordinary local filesystem inbox

No distributed infrastructure should be necessary.

In particular, the initial implementation should not require:

- PostgreSQL
- Redis
- Kafka
- Celery
- Kubernetes
- object storage
- distributed queues
- a JavaScript SPA
- bidirectional file synchronization

These can be introduced later only if concrete requirements justify them.

---

# 39. Core Design Principles

## Low-friction capture

Sending something to the system should require almost no thought.

```text
Share → Telegram → done
```

## Server independence from laptop availability

Capture and processing must continue regardless of whether the laptop is online.

## Durable processing state

A restart should not lose track of what has already happened.

## Idempotent ingestion

Repeated Telegram delivery must not create duplicate captures.

## Files for content, SQLite for state

The filesystem stores the actual information.

SQLite stores what the system knows about the processing of that information.

## Pull-based delivery

The laptop decides when to retrieve processed content.

## Explicit acknowledgement

Deleting a local file should not implicitly delete the server's copy.

## Human-readable output

Exported items should remain useful without the Info Triage application itself.

## Manual final decision

Automatic processing assists organization but does not replace the final human review step.

## Simple infrastructure

This is a personal system. Operational complexity should only be introduced when it solves a demonstrated problem.
