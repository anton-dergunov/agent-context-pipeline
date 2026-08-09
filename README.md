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
    -> source and media saved immediately in data/staging/
    -> direct to inbox when no processing step applies
       or queued for the single background worker
    -> optionally choose ML, Career, Life, or Other
    -> NAS data/inbox/<YYYY-MM-DD>_<message_id>/
    -> ./sync.sh
    -> ~/info-triage-inbox/
```

The directory date is the UTC creation date supplied by Telegram.

Each completed item contains `message.md`, `metadata.json`, and the complete
received Telegram payload in `telegram.json`. Useful source media is kept in
`attachments/`: documents, photos, video, animations, voice/audio notes, and
video notes. Albums are one ordered item. A label is optional; selecting one
updates the ready item and adds category front matter. Telegram message edits
update the same stable item directory.

The capture layer deliberately ignores Telegram interaction content such as
stickers, contacts, polls, payments, games, dice, and service events. It stores
original media only: there is no transcription, OCR, image analysis, or web
page extraction. Files above Telegram's hosted Bot API download limit are
recorded in metadata with a warning but cannot be copied locally.

The Python runtime remains deliberately small. `app.py` only wires together
the application. The `info_triage/` package separates shared models, storage,
processing, Telegram handling, and the read-only web dashboard. SQLite's
`received` rows are the durable processing queue, and one background thread
processes at most one item at a time. There are currently no processing steps,
so captured items normally move from staging to inbox immediately.

Future processors will produce the laptop-facing `message.md` in a temporary
revision-specific workspace. Raw Telegram data remains in `telegram.json`, and
original downloaded media remains in `attachments/`. A result is committed
only if its source revision is still current.

Create a local `.env` (it is ignored by Git):

```dotenv
TELEGRAM_BOT_TOKEN=your-token
ALLOWED_USER_ID=your-numeric-telegram-user-id
```

Run locally in Docker:

```bash
./run.sh
curl http://localhost:8000/health
```

The read-only processing dashboard is available at `http://localhost:8000/`
locally and `http://192.168.1.10:8000/` on the NAS.

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
NAS copy. If the Telegram message is edited or labeled later, its higher
revision is downloaded again.

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

The Telegram interface can also provide explicit classification options through buttons or menus. This allows two complementary modes:

1. Automatic classification — simply send something and let the server determine what it is.
2. Manual classification — explicitly identify an item as a job, article, task, reference, read-later item, etc.

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

Explicit user classification, when provided, can override or supplement automatic classification.

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
