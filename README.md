# Agent context pipeline

A self-hosted pipeline between what I come across during the day and the coding agent that files it. Capturing something takes one tap; the pipeline retrieves it, turns it into clean Markdown, and delivers an inbox the agent can work through without fetching anything itself.

## What it does

- **Capture from any device.** Share to a Telegram bot from the phone's share sheet, press a shortcut in Chrome ([`extension/`](extension/)), or send from the command line or a script.
- **Extraction to clean Markdown.** Web pages, PDFs and research papers, plus dedicated handling for YouTube, Instagram, LinkedIn and Medium. Text shown in images and video is recovered by OCR, and speech by transcription, both running locally.
- **Links and titles resolved.** Shortened links are followed, tracking parameters are dropped, and bare links get their page titles.
- **Related notes looked up ahead of the agent.** Each item arrives with pointers to the existing notes and plans it most likely belongs with, so the agent starts from candidates instead of a search. [`docs/architecture/related-notes.md`](docs/architecture/related-notes.md) describes it, and [`experiments/related-notes/`](experiments/related-notes/README.md) measures what it saves in tokens.
- **One file per item.** Every item is a self-contained directory, and its `index.md` is the only file the agent has to read: what was captured, what was retrieved and how long each body is, and what went wrong if anything did.
- **Nothing captured is lost.** Everything after capture is enrichment. A step that fails is recorded on the item, and the item still arrives.

Capturing is immediate and deciding is deferred: what to do with an item is settled later, in a review session with the agent, not at the moment of saving it.

The Python package and its commands keep the project's original name, `info-triage`.

## How it works

```text
Telegram message to one of four bots, or POST /capture
    -> nearby messages are grouped into one item
    -> saved durably on the server
    -> that route's steps run: transcribe, clean, find links, resolve, extract, render index.md
    -> server inbox:   data/inbox/<route>/<YYYY-MM-DD>_<n>/
    -> ./sync.sh
    -> laptop inbox:   ~/info-triage-inbox/<route>/  with triage.md and triage.org
    -> review with the agent; removing an item's directory marks it processed
```

The server is an always-on machine on the home network, a Synology NAS here, running one small container. The laptop syncs when convenient.

## Routes

Which bot something is shared to decides how it is processed and where it lands. That is one tap in the share sheet, the same as sharing anywhere else, and there is no follow-up question to answer.

| Route | For | Automatic processing |
|---|---|---|
| `info` | Things to think about and file later | Everything: transcription, cleaning, link resolution, retrieval, index |
| `job` | Job postings | Records the link. Nothing is fetched or rewritten |
| `clip` | Tango and photography clips | Records the link. Downloading happens on the laptop |
| `lang` | Vocabulary and phrases, with their context | Nothing. The wording is the point |

Each route has its own bot, its own inbox directory, and its own queue on the laptop with its own numbering.

Shared to the wrong bot? Edit the message and add `#job`, `#clip`, `#lang` or `#info`. The item moves to that route, is renumbered there, and re-runs that route's processing. Two route hashtags at once change nothing.

## Capturing

### From Telegram

Share or forward anything to one of the bots: text, links, forwarded posts, documents, photos, video, voice notes, locations. Messages sent within three seconds of each other become one item, so a link followed by a typed comment arrives together. Editing a message later updates the same item. The bot stays silent unless a capture fails.

### From the command line or a script

`POST /capture` takes the same item from anywhere:

```bash
export INFO_TRIAGE_CAPTURE_URL=http://<server>:8000
export INFO_TRIAGE_CAPTURE_TOKEN=…            # from .env

info-triage-capture --route job "https://example.com/posting  worth a look"
echo "sobremesa — the talk after a meal" | info-triage-capture --route lang
info-triage-capture --file spec.pdf "the spec I mentioned"
```

Each capture prints the handle of the item it created, such as `job/2026-08-18_4`. Passing that handle back rewrites the item instead of capturing a second one:

```bash
info-triage-capture --id job/2026-08-18_4 "the posting, with the note I meant"
info-triage-capture --id job/2026-08-18_4 --route clip "actually just worth reading"
```

A rewrite replaces the whole item: attachments not attached again are dropped, and the route's processing runs from scratch. Re-filing it under another route renumbers it, so the printed handle is the one to keep. The capture time cannot be changed, and an item captured from Telegram is edited by editing the Telegram message.

The endpoint requires a bearer token and is otherwise reachable from the whole LAN, as the dashboard is. Reaching it from elsewhere is a job for Tailscale or the equivalent.

### From the browser

[`extension/`](extension/) is a Chrome extension that sends the current page, any selected text and a note to the same endpoint. Load it unpacked from that directory, set the server address and token on its options page, and press `Ctrl+Shift+K` (`Command+Shift+K` on a Mac). The dialog stays open after sending, and sending again updates the same item.

## Quick start

```bash
uv sync
cp .env.example .env      # four bot tokens, your Telegram user id, a capture token
./run.sh                  # docker compose up --build
curl http://localhost:8000/health
```

The dashboard at `http://localhost:8000/` shows what has arrived, what is processing and what each step has made of it. Creating the bots, the settings in `config.yaml` and the optional extras are in [`docs/operations/setup.md`](docs/operations/setup.md).

Deploy to the NAS, after the one-time setup in [`docs/operations/synology-deployment.md`](docs/operations/synology-deployment.md):

```bash
./deploy.sh
```

On the laptop:

```bash
uv sync --extra neighbours
./sync.sh
```

## Reviewing

Each sync leaves, in every route's directory, the item directories and two generated views of them, numbered the same way:

- `triage.md` is the one the agent reads: the items' `index.md` files in order, one `### N — <id>` section each.
- `triage.org` is navigation for a person in Emacs, a line or two per item. See [`docs/reviewing-in-emacs.md`](docs/reviewing-in-emacs.md).

Pick items by number ("route items 1, 5 and 10"). Moving or deleting an item's directory is the only way to mark it processed; the next sync removes it from the server. If the original message is edited later, the item comes back.

## Documentation

- [`docs/`](docs/README.md) — architecture, the item contract, the preprocessing catalogue, the extractors, deployment.
- [`experiments/`](experiments/README.md) — the measurements behind the design, with their apparatus and results.
