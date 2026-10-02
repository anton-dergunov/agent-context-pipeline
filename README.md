# Agent context pipeline

A self-hosted pipeline between what you come across during the day and the coding agent that files it. Capturing something takes one tap; the pipeline retrieves it, turns it into clean Markdown, and delivers an inbox the agent can work through without fetching anything itself.

![One item's way through the pipeline: a LinkedIn post is captured from the browser with a selected quote and an intent; the server cleans it, finds and resolves its link, extracts the post and follows the author's comment to the arXiv paper it discusses, then renders index.md; ./sync.sh brings it to the laptop and adds possible neighbours from the user's own notes; the item appears as number 7 in triage.org, and the agent is asked to file item 7.](assets/overview.png)

## What it does

- **Capture from any device.** Share to a Telegram bot from the phone's share sheet, press a shortcut in Chrome, or send from the command line or a script.
- **Extraction to clean Markdown.** Web pages, PDFs and research papers, plus dedicated handling for YouTube, Instagram, LinkedIn and Medium. Text shown in images and video is recovered by OCR, and speech by transcription, both running locally.
- **Links and titles resolved.** Shortened links are followed, tracking parameters are dropped, and bare links get their page titles.
- **Related notes looked up ahead of the agent.** Each item can arrive with pointers to the existing notes and plans it most likely belongs with, so the agent starts from candidates instead of a search. [`docs/architecture/related-notes.md`](docs/architecture/related-notes.md) describes it, and [`experiments/related-notes/`](experiments/related-notes/README.md) measures what it saves in tokens.
- **One file per item.** Every item is a self-contained directory, and its `index.md` is the only file the agent has to read: what was captured, what was retrieved and how long each body is, and what went wrong if anything did.
- **Nothing captured is lost.** Everything after capture is enrichment. A step that fails is recorded on the item, and the item still arrives.

Capturing is immediate and deciding is deferred: what to do with an item is settled later, in a review session with the agent, not at the moment of saving it.

The repository is `agent-context-pipeline`; the program inside it is called `info-triage`, which is the name its commands, settings and folders carry.

## How it works

```text
a Telegram bot, the browser extension, or POST /capture
    -> nearby Telegram messages are grouped into one item
    -> saved durably on the server
    -> that route's steps run: transcribe, clean, find links, resolve, extract, render index.md
    -> server inbox:   data/inbox/<route>/<YYYY-MM-DD>_<n>/
    -> ./sync.sh
    -> laptop inbox:   ~/info-triage-inbox/<route>/  with triage.md and triage.org
    -> review with the agent; removing an item's directory marks it processed
```

The server is one small container on any machine that runs Docker and stays on: a home server, a NAS, a rented VPS. It can also be the laptop itself, in which case nothing is copied anywhere and the same commands work.

## Install

You need [Docker](https://docs.docker.com/get-docker/) where the server runs, and [uv](https://docs.astral.sh/uv/) on the machine you review from. The full walkthrough is [`docs/operations/setup.md`](docs/operations/setup.md).

### 1. Start the server

```bash
git clone https://github.com/anton-dergunov/agent-context-pipeline.git
cd agent-context-pipeline
cp .env.example .env                  # set INFO_TRIAGE_CAPTURE_TOKEN: openssl rand -base64 32
cp config.example.yaml config.yaml    # the routes and their processing; fine as it is
./run.sh                              # docker compose up --build; the first build downloads the models
```

`http://localhost:8000/` is the dashboard: what has arrived, what is processing and what each step made of it. No Telegram setup is needed for this first run; the browser extension and the command line can already capture.

To run it on another machine, name that machine in `.env` and deploy from here:

```dotenv
INFO_TRIAGE_SERVER=my-server                # an alias from ~/.ssh/config, or user@host
INFO_TRIAGE_SERVER_DIR=/srv/info-triage     # the project directory there
```

```bash
./deploy.sh        # copy the project over SSH, rebuild, restart, wait for the health check
```

[`docs/operations/deployment.md`](docs/operations/deployment.md) covers what the server needs, and [`docs/operations/synology.md`](docs/operations/synology.md) the extra steps on a Synology NAS.

### 2. Add Telegram bots (optional)

Sharing to a bot is the one-tap path from a phone. Create a bot per route with [@BotFather](https://t.me/BotFather), put each token in `.env`, and uncomment that route's `token_env` line in `config.yaml`. [`docs/operations/setup.md`](docs/operations/setup.md#5-telegram-bots) has the steps, and [`assets/bot-icons/`](assets/bot-icons/) a set of profile pictures that tell the bots apart in the share sheet.

### 3. Install the browser extension

Download the zip from [Releases](https://github.com/anton-dergunov/agent-context-pipeline/releases), unpack it, and load the folder at `chrome://extensions` with Developer mode on. Enter the server URL and the capture token on its options page. [`extension/README.md`](extension/README.md) has the details.

### 4. Bring the inbox to your laptop

```bash
uv sync
./sync.sh
```

This fills `~/info-triage-inbox/` from the server named in `.env`, or from this checkout's own `data/` when none is named. Run it whenever you want to review.

## Capturing

### From Telegram

Share or forward anything to one of the bots: text, links, forwarded posts, documents, photos, video, voice notes, locations. Messages sent within three seconds of each other become one item, so a link followed by a typed comment arrives together. Editing a message later updates the same item. The bot stays silent unless a capture fails.

### From the browser

Press `Ctrl+Shift+K` (`Command+Shift+K` on a Mac) on any page. The dialog carries the page address and any selected text; pick a route, add a note, send. It stays open afterwards, and sending again updates the same item.

### From the command line or a script

`POST /capture` takes the same item from anywhere, and `info-triage-capture` is its client:

```bash
uv run info-triage-capture "https://example.com/article  worth a look"
uv run info-triage-capture --route job "https://example.com/posting"
echo "a thought to keep" | uv run info-triage-capture
uv run info-triage-capture --file spec.pdf "the spec I mentioned"
```

It reads the token from `.env`, and the server address from `INFO_TRIAGE_CAPTURE_URL` there (`http://localhost:8000` when unset). Each capture prints the handle of the item it created, such as `job/2026-08-18_4`. Passing that handle back rewrites the item instead of capturing a second one:

```bash
uv run info-triage-capture --id job/2026-08-18_4 "the posting, with the note I meant"
uv run info-triage-capture --id job/2026-08-18_4 --route info "actually just worth reading"
```

A rewrite replaces the whole item: attachments not attached again are dropped, and the route's processing runs from scratch. Re-filing it under another route renumbers it, so the printed handle is the one to keep. The capture time cannot be changed, and an item captured from Telegram is edited by editing the Telegram message.

The endpoint requires the bearer token and is otherwise reachable from the whole local network, as the dashboard is. Reaching it from elsewhere is a job for a private network such as Tailscale: [`docs/operations/tailscale-https.md`](docs/operations/tailscale-https.md).

## Routes

A route decides how something is processed and where it lands. Each has its own inbox directory, its own queue on the laptop with its own numbering, and usually its own bot, so choosing a route is the one tap of choosing whom to share to. Routes are defined in `config.yaml`, as many as you like and under any name. The template ships three:

| Route | For | Automatic processing |
|---|---|---|
| `info` | Things to think about and file later | Everything: transcription, cleaning, link resolution, retrieval, index |
| `job` | Job postings | Records the link. Nothing is fetched or rewritten |
| `clip` | Video clips to download later | Records the link. Downloading happens on the laptop |

Add your own by copying one of these and listing the steps it should run; the first route in the file is the default for a capture that names none.

Shared to the wrong bot? Edit the message and add the route's name as a hashtag, such as `#job`. The item moves to that route, is renumbered there, and re-runs that route's processing. Two route hashtags at once change nothing.

## Reviewing

Each sync leaves, in every route's directory, the item directories and two generated views of them, numbered the same way:

- `triage.md` is the one the agent reads: the items' `index.md` files in order, one `### N — <id>` section each.
- `triage.org` is a compact list for a person, one or two lines per item.

Point your agent at `~/info-triage-inbox/info/triage.md` and pick items by number ("file items 1, 5 and 10"). Moving or deleting an item's directory is the only way to mark it processed; the next sync removes it from the server. If the original message is edited later, the item comes back.

No particular editor is required. [`docs/reviewing.md`](docs/reviewing.md) describes the workflow, including an optional Emacs integration for working the queue with single keys.

## Documentation

- [`docs/operations/setup.md`](docs/operations/setup.md) — installing, the settings in `.env` and `config.yaml`, defining routes.
- [`docs/`](docs/README.md) — architecture, the item contract, the preprocessing catalogue, the extractors, deployment.
- [`experiments/`](experiments/README.md) — the measurements behind the design, with their apparatus and results.

## License and credits

[MIT](LICENSE). The bot and extension icons are from [Flaticon](https://www.flaticon.com/) and keep their own licence: [Reading](https://www.flaticon.com/free-icons/reading), [Languages](https://www.flaticon.com/free-icons/languages) and [Video](https://www.flaticon.com/free-icons/video) icons created by Magnific, and [Job](https://www.flaticon.com/free-icons/job) icons created by surang.
