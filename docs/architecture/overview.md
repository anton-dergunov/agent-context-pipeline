# Architecture overview

A small self-hosted system that captures what its owner comes across during the day, enriches it on
an always-on server, and delivers it to a laptop as an inbox a coding agent can work through.

> **Capture quickly → process on the server → synchronize to the laptop → decide later**

It is not a task manager, a knowledge base or a read-later service. Its job ends when an item is in
the laptop inbox with everything worth knowing about it already retrieved. The Python package and its
commands keep the project's original name, `info-triage`.

The stated failure mode for this project is generalizing it into a platform. Implementations stay
direct, and a framework, service or abstraction is added only when a concrete requirement needs it.

## The delivery guarantee

**Nothing captured is ever withheld because preprocessing went wrong.** Everything after capture —
transcription, cleaning, link discovery, resolution, extraction, index rendering — is enrichment. If
something was sent and never reaches the laptop, no amount of successful enrichment compensates for
it. Reading the raw source is an acceptable outcome; losing it is not.

- A processing step never fails an item. A declared `partial` or `failed` outcome and an unexpected
  exception are treated alike: recorded, rolled back if they changed the result, and the pipeline
  continues with the next step. `ProcessingWorker._process` is where this is enforced.
- A step that hands over an unusable generated file (missing, outside its workspace, or an unsafe
  path inside the item) costs that step's output, never the item.
- A failure is reported in three places: `data/logs/processor-runs.jsonl` with the exception type and
  traceback, the dashboard, and the item's own `index.md` under `## Problems`.
- Only two failures stop an item, because they leave nothing to deliver: its capture text cannot be
  read, or the commit into the inbox fails. Both keep the item in `data/staging/` as `failed`.
- When in doubt, the item ships with a field omitted and a problem recorded. A value is never guessed
  to fill a gap, and an item is never held back to get a better one.

## Components

```text
Telegram bots (one per route)        POST /capture (CLI, browser extension, scripts)
            │                                   │
            └──────────────┬────────────────────┘
                           ▼
Server ── python -m info_triage
   ├── capture            durable staging in data/staging/<route>/
   ├── one worker thread  runs the route's ordered steps, one item at a time
   ├── SQLite             item state, source-to-item mapping, processor counters
   ├── HTTP server        GET /health, GET / (dashboard), POST /capture
   └── data/inbox/<route>/   finished items
                           │
                           │  sync.sh  (rsync over SSH, driven from the laptop)
                           ▼
Laptop ── ~/info-triage-inbox/<route>/
   ├── item directories, each with its index.md
   ├── triage.md          the items' indexes, concatenated: what the agent reads
   └── triage.org         navigation for a person in Emacs
                           │
                           ▼
   Review with the agent; removing an item's directory marks it processed,
   and the next sync removes it from the server.
```

`src/info_triage/__main__.py` is the only runtime entry point and only wires things together. The long-lived execution
paths are one asyncio event loop that polls every route's bot, one processing-worker thread, and the
HTTP server, which is threaded so that a slow upload cannot block the health check.

| Module | Holds |
|---|---|
| `src/info_triage/telegram_bot.py` | Telegram capture, grouping, edits, route hashtags |
| `src/info_triage/capture_api.py`, `capture_cli.py` | the `POST /capture` contract and its shipped client |
| `src/info_triage/storage.py` | SQLite and the item directories |
| `src/info_triage/processing.py` | the worker, the step loop and telemetry |
| `src/info_triage/preprocessing.py` | the configured steps |
| `src/info_triage/rendering.py` | Telegram payload to segments |
| `src/info_triage/links.py` | offline link discovery, canonicalization, ranking |
| `src/info_triage/extraction.py` | the extraction cache and handler dispatch |
| `src/info_triage/index.py` | intent detection and the `index.md` contract |
| `src/info_triage/extractors/` | the content extractors, also usable standalone |
| `src/info_triage/utilities/` | text cleaning, URL resolution, Markdown helpers |
| `src/info_triage/web.py` | health check, dashboard, capture endpoint |
| `src/info_triage/sync.py`, `neighbours.py` | the laptop side; they never import the daemon |

Daemon settings are read strictly from `config.yaml` (or the file named by `INFO_TRIAGE_CONFIG`):
unknown fields and invalid values stop startup. `.env` holds what differs between installations: the
secrets (a bot token per route that has a bot, the allowed Telegram user id, the capture bearer
token), where the server is, and the laptop's own settings. The daemon reads only the first group.

## Routes

A route is a capture-time pipeline switch: one ordered step list, one `data/inbox/<route>/` tree,
one laptop queue, and usually one Telegram bot. Which bot a message is shared to decides the other
three, at no cost beyond the share itself.

Routes are whatever `config.yaml` declares under `routes`: any number of them, each named with a
lower-case letter followed by up to 31 lower-case letters, digits or underscores, because the name
is at once a directory, the first half of an item handle and a hashtag. The first route declared is
the default for a capture that names none. `config.example.yaml` ships three:

| Route | For | Steps |
|---|---|---|
| `info` | things to think about and file later | all six |
| `job` | job postings | `link-discovery`, `index-render` |
| `clip` | video clips to download on the laptop | `link-discovery`, `index-render` |

Only `info` cleans, resolves, retrieves, transcribes or OCRs. On the other two the captured text
passes through untouched, because something downstream already processes it. A route that only
keeps the wording, such as a vocabulary list, needs `index-render` alone.

A route's bot is optional. A route that declares no `token_env` is fed by `POST /capture` only, so
the daemon runs with no Telegram bot at all when the browser extension or the command line is the
only way in.

An item carries three route fields, and they are not interchangeable:

- `origin_route` is the bot that owns the Telegram message. It is part of the primary key and never
  changes.
- `route` is where the item is filed. A hashtag can move it.
- `local_id` is the number in the directory name. It is allocated per route and reallocated on a move.

Editing a captured message to include a configured route's name as a hashtag, such as `#job`,
moves the item: its
directory is renamed under the destination route, its revision increases, and it runs that route's
pipeline. Hashtags are read from Telegram's own `hashtag` entities, never from the raw text, so a
`#clip` inside a URL fragment is not an instruction. Two different route hashtags at once change
nothing. After a move, later edits still arrive on the original bot, so lookups go by `origin_route`.

Two routes may not share a token variable: two pollers on one bot produce a conflict loop in which
both silently miss messages. A declared token that is missing or rejected stops the daemon instead
of leaving one route unpolled. A route with no configured pipeline delivers its
items unprocessed and logs loudly; a configuration mistake may not withhold a capture.

In this codebase "route" has one other, unrelated meaning: `extractors/router.py:route_url()`
decides which extractor handles a URL.

## Capture

### Telegram

The bots accept text, links, forwards, documents, photos, video, animations, voice and audio notes,
video notes, locations and venues. They ignore stickers, contacts, polls, payments, games, dice and
service events. A successful capture is silent; only a capture failure produces a reply.

Sharing a link and then typing a comment produces two Telegram messages, so messages are first
written to a durable pending table and grouped: consecutive messages whose timestamps are at most
`max_gap_seconds` (3) apart become one item once `settle_seconds` (4) have passed without another. A
media album counts as one message. Pending captures survive a restart.

Media is stored as supplied. The hosted Bot API limits downloads to 20 MiB; a larger file is recorded
in the item's metadata with a warning and cannot be copied. Forwarding provenance is taken only from
Telegram's explicit `forward_origin`. The application never guesses which message is the owner's own
commentary.

### `POST /capture`

The transport-independent entry point, which makes Telegram one client of the system:

```text
POST /capture   Authorization: Bearer <token>
{route?, source, text, captured_at?, files?, id?}
→ 201 {route, id, revision, status}
```

One request is one item; the grouping window does not apply. A request that names no `route` goes
to the first route declared, so a client never has to know which routes an installation defines. `info-triage-capture` is the shipped
client and the Chrome extension in `extension/` is another.

Passing back an `id` (the `<route>/<name>` handle a capture answered with) rewrites that item and
answers `200`. It is a replacement, not a patch: the body is the item's whole new content, files it
does not repeat are gone, and everything the previous revision generated is discarded before the
pipeline runs again. `route` beside an `id` is the destination, so a replacement can re-file an item,
which renumbers it; left out, the item stays in the route its handle names. `captured_at` cannot change. Only HTTP captures are rewritable; a Telegram
capture answers `409`, because its message still exists upstream and is edited there.

An HTTP capture writes `capture/payload.json` in Telegram's own payload shape, so link discovery,
segment rendering and index rendering read it unchanged. Its identity is `chat_id = 0` (private-chat
ids are always positive) with `message_id` set to the allocated `local_id`.

The bearer token is the only access control. The port is published to the whole LAN and the
dashboard is unauthenticated. `Content-Length` is checked before the body is read; a request is at
most 32 MiB, a file 20 MiB, and a capture carries at most 20 files.

## Item identity and naming

An item is identified by `(origin_route, chat_id, message_id)`. A private chat's `chat_id` is the
user's own id and is the same on every bot, and each bot's `message_id` counter starts small, so the
route is what makes the triple unique. When messages are grouped, the earliest one is the item's
identity and every constituent message maps to it.

The directory is named `YYYY-MM-DD_<local_id>`, from the earliest source's UTC creation date. The
number is a per-route counter, not the Telegram message id: an HTTP capture has none, and a re-routed
item would carry a number its destination route may already have used. The directory layout and
`index.md` are described in [`item-contract.md`](item-contract.md).

## Lifecycle

| State | Where the item is | Meaning |
|---|---|---|
| `received` | `data/staging/<route>/` | saved and waiting; these rows are the durable queue |
| `processing` | `data/staging/<route>/` | claimed by the worker; `processing_step` names the running step |
| `ready` | `data/inbox/<route>/` | delivered to the inbox, with or without problems |
| `failed` | `data/staging/<route>/` | nothing deliverable: unreadable capture text, or the commit failed |

An item to which no configured step applies goes from `received` to `ready` without occupying the
worker. On restart, an interrupted `processing` item still in staging returns to `received`. There
are no marker files; SQLite says which state a staged item is in, and anything in `inbox/` is ready.

## Processing

One background thread claims the oldest `received` row and processes one item at a time, across
every route. The steps are ordinary Python classes built from the route's `steps` list in
`config.yaml` and run in exactly that order. [`preprocessing.md`](preprocessing.md) is the catalogue
of what each one does.

Each step receives a fresh temporary workspace and a snapshot of the accumulated result. It returns
`succeeded`, `partial` (usable output with recoverable issues, kept) or `failed` (its changes are
discarded). New files are handed over as `GeneratedFile(relative_path, source_path)`, and the source
must live inside the step's workspace.

A revision is always rebuilt from the retained payload, never from a previously processed file, and
is committed only if it is still the current revision. A later edit therefore supersedes a slow
result without blocking capture. Because of that rebuild, expensive retrieval is cached outside the
item under `data/extraction-cache/`, keyed on the canonical URL: adding a note to a message must not
download its paper again.

### Telemetry

Every step runs through the shared worker, so its runs are logged and counted in one place.

- `data/logs/processor-runs.jsonl` is append-only, one event per line, with no rotation or cleanup.
  A successful run records status, duration, the step and the item's identity. A problem run also
  records stable reason keys, the untruncated step input, the failed target, and the exception type
  and traceback when there was one. Transformed results and binary media are never logged.
- SQLite keeps cumulative succeeded, partial and failed counts per step, and per reason key. They are
  keyed by step name alone: whether `url-resolution` is healthy is a question about the whole system,
  and the route is on every log record for anyone who needs it per route.
- Every outcome other than `succeeded` also becomes a problem on the item, listed in its `index.md`.

### Edits

Editing any constituent Telegram message updates the same item. The edited member's payload and
attachments are replaced, the revision increases, the item moves back to staging if it had been
delivered, and the pipeline runs again. Unedited members stay as they were.

## SQLite

`data/info-triage.sqlite3` is operational state, not content:

| Table | Holds |
|---|---|
| `items` | one row per item: the three route fields, state, revision, timestamps, a short preview, the error, the running step, the problems it was delivered with |
| `item_messages` | every constituent source message mapped to its item |
| `pending_capture_messages` | raw payloads waiting out the grouping window, deleted once written to an item |
| `route_sequences` | the per-route `local_id` counter, shared by every transport |
| `processor_stats`, `processor_reason_stats` | the cumulative counters |

Text, media and generated output stay on disk. The database is not synchronized to the laptop. The
store refuses a database that predates routes instead of migrating it.

## HTTP

- `GET /health` is a cheap check, used by the container health check and by `deploy.sh`.
- `GET /` is a read-only dashboard over SQLite: one tab per item state with its count, and a
  Processors tab with lifetime outcome totals and reason keys that can be pasted into `grep -F`
  against the run log. Item rows show route, id, timestamps, revision, a preview, the
  running or failed step and the problems the item carried. The page reloads every ten seconds.
- `GET /routes` answers `{"routes": [...], "default": "<first route>"}` to a request carrying the
  capture bearer token. It is how a capture client learns the routes instead of hard-coding them,
  and how it checks its address and token without leaving a test item behind.
- `POST /capture` is the only mutating endpoint.

## The laptop side

`sync.sh` downloads new and edited items, removes from the server what was processed locally, and
regenerates the two views of each route's queue. [`sync.md`](sync.md) describes it, and
[`related-notes.md`](related-notes.md) the annotation pass that follows a sync.

## What the system deliberately does not have

- a database server, a message broker, a task queue or object storage
- export, acknowledgement or archive state: removing a directory is the whole protocol
- server-side retention of consumed content
- marker files, an events table or a processing timeline
- a front-end application, or any way to act on items from the dashboard
- classification or destination inference at capture time
- a language model anywhere in the pipeline; see [`item-contract.md`](item-contract.md#why-nothing-is-summarized)

Content lives in files because files are easy to inspect, copy and search with any tool. State lives
in SQLite only where it is awkward to infer from files. One item is one directory, so everything that
belongs to a capture travels together.
