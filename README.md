# Personal Information Triage and Capture System

## Overview

A self-hosted application for capturing information from different devices and sources, processing it automatically, and routing it into a central inbox for later review.

The system acts as a universal capture and triage layer between information encountered during the day and the tools where that information will eventually be stored or acted upon.

Instead of immediately deciding what to do with something, I can simply share it to the server. The server retrieves the content, extracts useful information, classifies it, performs initial processing, and stores it for later manual review.

The goal is to make capturing information extremely low-friction while keeping the final organization and decision-making process under manual control.

## Current Prototype

The current implementation is deliberately small:

```text
Telegram message
    -> wait for the configurable nearby-message grouping window
    -> grouped source and media saved in data/staging/
    -> queued for the configured single-worker pipeline
       (or direct to inbox when every configured step is inapplicable)
    -> category: Other
    -> NAS data/inbox/<YYYY-MM-DD>_<message_id>/
    -> ./sync.sh
    -> ~/info-triage-inbox/ item directories + generated inbox.md
```

The directory date is the UTC creation date supplied by Telegram.

Each completed item contains a materialized `source.md`, processed `message.md`,
`metadata.json`, and the complete received Telegram payload in `telegram.json`.
Useful source media is kept in
`attachments/`: documents, photos, video, animations, voice/audio notes, and
video notes. Albums are one logical message. Consecutive logical messages whose
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
processes at most one item at a time. The shipped ordered pipeline performs
voice transcription when applicable, bounded URL/title enrichment, and text
cleaning.

Voice transcription first materializes the complete segmented body in
`source.md`. URL/title enrichment converts bare links to `[page title](URL)`
Markdown where bounded public HTML or PDF metadata/first-page text provides a
trustworthy title. Blocked ordinary requests get one anonymous
Chrome-compatible HTTP retry. Text
cleaning then produces the laptop-facing `message.md`; category front matter is
added only after those body transforms.
Raw Telegram data remains in `telegram.json`, and original downloaded media
remains in `attachments/`. Both Markdown files are committed only if their
source revision is still current.

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
[`docs/MEDIUM_EXTRACTION.md`](docs/MEDIUM_EXTRACTION.md).

Instagram and YouTube both depend on the neutral
`info_triage.extractors.media` package for OCR engines, sampled-frame OCR,
runtime thread tuning, and local speech transcription. Compatibility imports
remain at the former Instagram module paths for existing scripts.

Install the portable stack with `uv sync`. On a Mac workstation, Surya and
Apple-Silicon MLX remain available with:

```bash
uv sync --extra surya --extra mac-transcription
```

Copy `.env.example` to a local `.env` (it is ignored by Git) and set the two
credentials:

```dotenv
TELEGRAM_BOT_TOKEN=your-token
ALLOWED_USER_ID=your-numeric-telegram-user-id
```

Non-secret daemon settings live in the commented [`config.yaml`](config.yaml).
Its `processing.steps` list is literal and ordered: remove a step to disable it.
Relative paths are resolved from the configuration file's directory. Set
`INFO_TRIAGE_CONFIG` in `.env` only when using another file. Unknown fields,
invalid values, duplicate steps, and voice transcription after a text transform
fail startup rather than being silently ignored.

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
locally and `http://192.168.1.10:8000/` on the NAS. Its Processors tab shows
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

The synchronization script keeps each delivered Telegram item ID and revision
under `~/.local/state/info-triage/`. Removing a delivered item directory from
`~/info-triage-inbox/` marks that revision processed; the next sync removes its
NAS copy. If a constituent Telegram message is edited later, its higher revision
is downloaded again.

After each successful sync, `~/info-triage-inbox/inbox.md` is regenerated as a
single oldest-first view of the current items. Each section has the UTC capture
time, user-facing metadata, a link to the self-contained item directory, and the
processed Markdown message. The file is derived and overwritten on every sync;
moving or deleting an item directory remains the only way to mark it processed.

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
