# Agent context pipeline

A self-hosted pipeline between what I come across during the day and the coding agent that files it. Capturing something takes one tap; the pipeline retrieves it, turns it into clean Markdown, and delivers an inbox the agent can work through without fetching anything itself.

## What it does

- **Capture from any device.** Share to a Telegram bot from the phone's share sheet, press a shortcut in Chrome ([`extension/`](extension/)), or send from the command line or a script.
- **Extraction to clean Markdown.** Web pages, PDFs and research papers, plus dedicated handling for YouTube, Instagram, LinkedIn and Medium. Text shown in images and video is recovered by OCR, and speech by transcription, both running locally.
- **Links and titles resolved.** Shortened links are followed, tracking parameters are dropped, and bare links get their page titles.
- **Related notes looked up ahead of the agent.** Each item arrives with pointers to the existing notes and plans it most likely belongs with, so the agent starts from candidates instead of a search. [`docs/Related-notes-design.md`](docs/Related-notes-design.md) measures what that saves in tokens.
- **One file per item.** Every item is a self-contained directory, and its `index.md` is the only file the agent has to read: what was captured, what was retrieved and how long each body is, and what went wrong if anything did.
- **Nothing captured is lost.** Everything after capture is enrichment. A step that fails is recorded on the item, and the item still arrives.

Capturing is immediate and deciding is deferred: what to do with an item is settled later, in a review session with the agent, not at the moment of saving it.

The Python package and its commands keep the project's original name, `info-triage`.

## Current Prototype

The current implementation is deliberately small:

```text
Telegram message to one of four bots  (or POST /capture)
    -> wait for the configurable nearby-message grouping window
    -> grouped source and media saved in data/staging/<route>/
    -> queued for that route's single-worker pipeline
       (or direct to inbox when every configured step is inapplicable)
    -> category: Other
    -> NAS data/inbox/<route>/<YYYY-MM-DD>_<n>/
    -> ./sync.sh
    -> ~/info-triage-inbox/<route>/ item directories + generated triage.md/.org
```

The directory date is the UTC creation date supplied by Telegram, and `<n>`
counts the items that route has received.

### Routes

Which bot something is shared to decides how it is processed and where it lands.
That is one tap in the share sheet, the same as sharing anywhere else, and there
is no follow-up question to answer.

| Route | For | Automatic processing |
|---|---|---|
| `info` | Things to think about and file later | Everything: transcription, cleaning, link resolution, retrieval, index |
| `job` | Job postings | Records the link. Nothing is fetched or rewritten |
| `clip` | Tango and photography clips | Records the link. Downloading happens on the laptop |
| `lang` | Vocabulary and phrases, with their context | Nothing. The wording is the point |

Each route has its own bot, its own inbox directory, and its own queue on the
laptop with its own numbering.

Shared to the wrong bot? Edit the message and add `#job`, `#clip`, `#lang` or
`#info`. The item moves to that route, is renumbered there, and re-runs that
route's processing. Two route hashtags at once change nothing.

### Capturing without Telegram

`POST /capture` takes the same item from anywhere:

```bash
export INFO_TRIAGE_CAPTURE_URL=http://<server>:8000
export INFO_TRIAGE_CAPTURE_TOKEN=…            # from .env

info-triage-capture --route job "https://example.com/posting  worth a look"
echo "sobremesa — the talk after a meal" | info-triage-capture --route lang
info-triage-capture --file spec.pdf "the spec I mentioned"
```

Each capture prints the handle of the item it created, such as
`job/2026-08-18_4`. Passing that handle back rewrites the item rather than
capturing a second one:

```bash
info-triage-capture --id job/2026-08-18_4 "the posting, with the note I meant"
info-triage-capture --id job/2026-08-18_4 --route clip "actually just worth reading"
```

A rewrite replaces the whole item: attachments not attached again are dropped,
and the route's processing runs from scratch. Re-filing it under another route
renumbers it, so the printed handle is the one to keep. The capture time cannot
be changed — it is part of the handle — and an item captured from Telegram is
edited by editing the Telegram message.

The endpoint requires a bearer token and is otherwise reachable from the whole
LAN, exactly as the dashboard already is. Reaching it from elsewhere is a job
for Tailscale or the equivalent, not for the endpoint itself.

### Capturing from the browser

[`extension/`](extension/) is a Chrome extension that sends the current page,
any selected text and a note to the same endpoint. Load it unpacked from that
directory, set the server address and token on its options page, and press
`Ctrl+Shift+K` (`Command+Shift+K` on a Mac). The dialog stays open after
sending, and sending again updates the same item instead of capturing a second
one.

Each completed item keeps `metadata.json` at its root and everything captured
under `capture/`: a materialized `source.md`, a processed
`message.md`, the complete received payload in `payload.json`, and
useful source media in `capture/attachments/` — documents, photos, video,
animations, voice/audio notes, and video notes. Albums are one logical message. Consecutive logical messages whose
Telegram timestamps are no more than three seconds apart are combined into one
item after a four-second quiet period. Every constituent Telegram message is an
ordered `## Segment N — kind` section. Telegram-provided forwarding provenance
adds `forwarded` to the kind; the application does not guess which unmarked
segment is personal commentary. New captures silently receive the `Other`
category. Editing any constituent Telegram message updates the same stable item
directory.

The capture layer deliberately ignores Telegram interaction content such as
stickers, contacts, polls, payments, games, dice, and service events. Original
media is retained. Telegram voice notes are transcribed locally into their
`voice` segment; other media has no automatic OCR, image analysis,
transcription, or web-page extraction. Files above Telegram's hosted
Bot API download limit are recorded in metadata with a warning but cannot be
copied locally. The complete behaviour matrix is in
[`docs/PREPROCESSING.md`](docs/PREPROCESSING.md).

The hosted Bot API currently limits [`getFile` downloads](https://core.telegram.org/bots/api#getfile)
to 20 MiB, so the application's matching guard is a documented transport
constraint rather than a YAML setting. Raising a local number could not raise
Telegram's limit; a self-hosted local Bot API transport would be a separate
future change.

The Python runtime remains deliberately small. `app.py` only wires together
the application. The `info_triage/` package separates shared models, storage,
processing, Telegram handling, and the read-only web dashboard. SQLite's
`received` rows are the durable processing queue, and one background thread
processes at most one item at a time, across every route. The `info` route's
shipped ordered pipeline performs voice transcription when applicable, text
cleaning, and then bounded URL/title enrichment; the other three routes do
almost nothing, so the expensive work only ever runs for `info`.

Voice transcription first materializes the complete segmented body in
`capture/source.md`. Text cleaning runs next, so that invisible characters and
look-alike letters cannot hide a link from the step that follows. URL/title
enrichment then converts bare links to `[page title](URL)` Markdown where
bounded public HTML or PDF metadata/first-page text provides a trustworthy
title; blocked ordinary requests get one anonymous Chrome-compatible HTTP retry.
The result is the laptop-facing `capture/message.md`; category front matter is
added only after those body transforms.
The raw received payload remains in `capture/payload.json`, and original downloaded
media remains in `capture/attachments/`. Both Markdown files are committed only
if their source revision is still current.

Every configured step is instrumented centrally. The daemon appends compact
JSONL run records to `data/logs/processor-runs.jsonl` and keeps cumulative
success, partial, failure, and stable-reason counters in SQLite. Declared
processor problems do not block delivery: partial output is retained, while a
failed step is rolled back before later steps continue. Unexpected code errors
still fail the item.

Reusable processing components are available under `info_triage.extractors`
and `info_triage.utilities`: Instagram download/OCR/transcription, anonymous
public LinkedIn extraction, standalone YouTube metadata/caption/Short-media
extraction, layered Medium article extraction, cautious text cleaning, and
bounded URL/title enrichment. The local speech-transcription engine,
text cleaner, and link resolver are reused by the configured Telegram pipeline
and retain their standalone console commands. See
[`docs/INSTAGRAM_EXTRACTION.md`](docs/INSTAGRAM_EXTRACTION.md) and
[`docs/YOUTUBE_EXTRACTION.md`](docs/YOUTUBE_EXTRACTION.md),
[`docs/LINKEDIN_EXTRACTION.md`](docs/LINKEDIN_EXTRACTION.md), plus the Medium
access decision and commands in
[`docs/MEDIUM_EXTRACTION.md`](docs/MEDIUM_EXTRACTION.md). Generic linked HTML,
PDF, and provider-aware research-paper extraction is described in
[`docs/URL_EXTRACTION.md`](docs/URL_EXTRACTION.md).

Instagram and YouTube both depend on the neutral
`info_triage.extractors.media` package for OCR engines, sampled-frame OCR,
runtime thread tuning, and local speech transcription. Compatibility imports
remain at the former Instagram module paths for existing scripts.

Install the portable stack with `uv sync`. On a Mac workstation, Surya and
Apple-Silicon MLX remain available with:

```bash
uv sync --extra surya --extra mac-transcription
```

Copy `.env.example` to a local `.env` (it is ignored by Git) and set the
credentials — one bot token per route, plus the capture token:

```dotenv
TELEGRAM_BOT_TOKEN_INFO=your-token
TELEGRAM_BOT_TOKEN_JOB=your-token
TELEGRAM_BOT_TOKEN_CLIP=your-token
TELEGRAM_BOT_TOKEN_LANG=your-token
ALLOWED_USER_ID=your-numeric-telegram-user-id
INFO_TRIAGE_CAPTURE_TOKEN=a-long-random-string   # openssl rand -base64 32
```

Create the three new bots with BotFather's `/newbot`, send each a `/start` so
the chat exists, and pin all four chats in Telegram — the share sheet orders its
chat row by pinned-then-recent, so pinning is what puts all four in the top row.
Distinct profile pictures matter more than names at that size. A missing or
rejected token stops the daemon rather than leaving one route unpolled.

Non-secret daemon settings live in the commented [`config.yaml`](config.yaml).
Each route's `steps` list is literal and ordered: remove a step to disable it for
that route. Relative paths are resolved from the configuration file's directory.
Set `INFO_TRIAGE_CONFIG` in `.env` only when using another file. Unknown fields,
invalid values, duplicate steps within a route, two routes sharing a token
variable, and voice transcription after a text transform fail startup rather
than being silently ignored.

The shipped Compose port mapping and health check use the default YAML port
`8000`. If `web.port` changes, update those two infrastructure values to match;
the daemon does not read a separate `PORT` environment override.

The default transcription backend/model is explicit `faster-whisper` `small`.
The Docker build preloads the model named by `config.yaml`; changing it in an
offline deployment therefore requires rebuilding the image.

Run locally in Docker:

```bash
./run.sh
curl http://localhost:8000/health
```

The unified image preloads the reviewed Linux production models (RapidOCR and
multilingual `faster-whisper` small) during the build and runs offline at
runtime. The Surya and MLX extras are intentionally not installed in Docker.

The read-only processing dashboard is available at `http://localhost:8000/`
locally and `http://<server>:8000/` on the NAS. Its Processors tab shows
lifetime outcome totals and searchable failure-reason keys.

After completing the one-time NAS rename described in
[`docs/SYNOLOGY_SETUP.md`](docs/SYNOLOGY_SETUP.md), deploy with:

```bash
./deploy.sh
```

Only one process may poll a Telegram bot token at a time. Stop any locally
running copy of the old bot before starting the NAS deployment.

Download new/edited items with:

```bash
./sync.sh
```

The synchronization script keeps each delivered item's route, ID and revision
under `~/.local/state/info-triage/`. Removing a delivered item directory from
`~/info-triage-inbox/<route>/` marks that revision processed; the next sync
removes its NAS copy. If a constituent Telegram message is edited later, its
higher revision is downloaded again. An item that moved to another route loses
its copy in the route it left.

After each successful sync, each route gets two views regenerated beside its
items as a single oldest-first list, grouped under a heading per day. Every route
numbers from 1 independently. `triage.md` is the
one to read: each `### N — <id>` section carries the UTC capture time,
user-facing metadata, a quoted lead, and working links into the self-contained
item directory. `N` is what you select by ("route items 1, 5 and 10"), runs
straight through the days, and is renumbered on every sync; `<id>` names the
directory and does not change. `triage.org` is the same list, numbered the same
way, as Emacs navigation — two lines per item and no content beyond a one-line
label — see [docs/EMACS.md](docs/EMACS.md).

Both files are derived and overwritten on every sync; moving or deleting an item
directory remains the only way to mark it processed.

## Example Inputs

The system should accept several kinds of information.

### Job postings

When I encounter an interesting job advertisement, for example on LinkedIn, I can share its URL with the application.

The server captures the page and stores it as a job-related item. A separate processing pipeline can later extract structured information such as:

* company
* role
* location
* salary
* required skills
* technologies
* responsibilities
* other information useful for evaluating the position

### Articles and web pages

Web pages can be captured for different reasons:

* an article I have already read and want to keep
* something I want to read later
* a useful reference
* something that implies a future task or action

The server retrieves the page content, extracts the useful text and metadata, and categorizes it for later processing.

### Instagram and social media posts

Interesting social-media posts can be shared with the system.

Where possible, the capture pipeline extracts:

* post text / description
* images
* links
* author and source information
* other useful metadata

The extracted content can subsequently be cleaned, summarized, classified, and converted into a more permanent representation.

### Email

Interesting emails can be forwarded or otherwise submitted to the system.

The application extracts the useful content and treats the email as another captured item rather than requiring a separate workflow for email.

Text snippets and notes

Arbitrary text can also be submitted directly.

This could include:

* ideas
* reminders
* observations
* copied sections of documents or web pages
* tasks
* references
* things to investigate later

### Telegram

A Telegram bot can provide a particularly convenient capture interface.

I can send text, links, forwarded messages, images, or other content to the bot. The bot sends them to the central server for processing.

The current Telegram interface stays silent after successful capture and assigns
`Other`; classification is deferred to later processing or manual review.

## Processing Pipeline

Conceptually, captured information passes through a pipeline:

Capture → Retrieve → Extract → Classify → Enrich → Route → Review

1. Capture

Receive an item through one of several interfaces:

* mobile share sheet
* Telegram
* browser
* email
* API
* desktop tools

2. Retrieve

If the item references external content, retrieve the underlying information.

For example:

* download a web page
* retrieve an article
* obtain metadata
* capture social-media content
* download associated images

3. Extract

Convert the source into a normalized internal representation.

This may include:

* title
* URL
* source
* text
* images
* attachments
* timestamps
* metadata

The original input should also be retained where practical.

4. Classify

Determine what kind of item was captured and potentially why it was captured.

Example categories include:

* job
* article
* read later
* task
* reference
* social-media post
* email
* idea
* note

Future processing may supplement the default category automatically.

5. Enrich

Additional processing can prepare the item for later use.

Depending on the item type, this might include:

* cleaning extracted text
* summarization
* metadata extraction
* entity extraction
* structured field extraction
* tagging
* LLM-based analysis
* image understanding
* duplicate detection

Different categories can have their own processing pipelines.

6. Route

Processed items can be routed to different destinations or queues according to their category.

The capture system itself does not necessarily need to become the final task manager, knowledge base, or document store. Instead, it provides a common ingestion and processing layer in front of those systems.

7. Review

Items ultimately arrive in an inbox for manual processing.

The important distinction is that capture is immediate, while organization is asynchronous.

When I encounter something useful, I do not need to interrupt what I am doing to decide exactly where it belongs or what action it requires. I capture it immediately and make those decisions later during a dedicated review process.

## Design Principle

The application separates three concerns that are normally mixed together:

Capture — save something with minimal effort.

Understanding — automatically retrieve, classify, clean, and enrich it.

Decision — later decide manually what should actually happen to it.

The system therefore acts as a centralized information triage service rather than simply another read-later application or task manager.
