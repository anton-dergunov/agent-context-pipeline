# Setting it up

From a fresh clone to a working inbox, in the order things are needed. The short version is in the
repository [`README.md`](../../README.md#install); this page is the whole of it.

There are two roles, and one machine can play both:

- **The server** runs the daemon in a Docker container: it receives captures, processes them and
  keeps the finished items in `data/inbox/`. It should be a machine that stays on.
- **The laptop** is where you review. `sync.sh` copies the server's inbox to `~/info-triage-inbox/`
  and sends back which items you have dealt with.

Everything is driven from one checkout on the laptop. When the server is a different machine,
`deploy.sh` copies the project there; you never edit files on the server by hand.

## 1. Requirements

- [Docker](https://docs.docker.com/get-docker/) with Compose, on the machine that runs the server.
- [uv](https://docs.astral.sh/uv/) and `rsync` on the laptop. Python 3.12 to 3.14; uv fetches one if
  needed.
- For a separate server: SSH access to it with a key, and `rsync` on it.

## 2. The two local files

```bash
git clone https://github.com/anton-dergunov/agent-context-pipeline.git
cd agent-context-pipeline
cp .env.example .env
cp config.example.yaml config.yaml
```

Git ignores both, so they are yours to edit and survive updates of the repository.

- **`.env`** holds what differs between installations: secrets, where the server is, and the
  laptop's own settings. It is never committed and its contents are never printed.
- **`config.yaml`** holds the daemon's behaviour: the routes and their processing steps, and tuning
  values. It contains no secrets.

The one value to set before the first run is the capture token in `.env`:

```bash
openssl rand -base64 32      # paste the output as INFO_TRIAGE_CAPTURE_TOKEN
```

## 3. Run the server

### On this machine

```bash
./run.sh          # docker compose up --build, in the foreground
```

or, to leave it running in the background and wait until it is healthy:

```bash
./deploy.sh       # with INFO_TRIAGE_SERVER empty this builds and starts the container here
```

The first build takes several minutes: it downloads the OCR and transcription models into the image,
so that nothing is downloaded while the daemon runs. Then:

```bash
curl http://localhost:8000/health        # Info Triage is running
```

and the dashboard is at `http://localhost:8000/`.

On Linux, set `INFO_TRIAGE_UID` and `INFO_TRIAGE_GID` in `.env` to your own (`id -u`, `id -g`) so
that the container writes `data/` as you. Docker Desktop on macOS and Windows needs no change.

### On another machine

Set two values in `.env` and deploy:

```dotenv
INFO_TRIAGE_SERVER=my-server                # an alias from ~/.ssh/config, or user@host
INFO_TRIAGE_SERVER_DIR=/srv/info-triage     # absolute path of the project directory there
```

```bash
./deploy.sh
```

[`deployment.md`](deployment.md) describes what the server needs and what the script does, and
[`synology.md`](synology.md) the extra steps on a Synology NAS.

## 4. Capture something

With the server up, the command-line client already works:

```bash
uv sync
uv run info-triage-capture "https://example.com/  my first capture"
```

It reads the token from `.env`. When the server is another machine, tell it where:

```dotenv
INFO_TRIAGE_CAPTURE_URL=http://my-server:8000
```

The item appears on the dashboard, first under Processing and then under Ready.

The browser extension is installed from the repository's Releases page; see
[`extension/README.md`](../../extension/README.md).

## 5. Telegram bots

Optional, and the most convenient way to capture from a phone. Each route can have its own bot, so
that choosing where something goes is the same tap as choosing whom to share it to.

For each route that should have one:

1. In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot` and follow it. It answers
   with a token.
2. Put the token in `.env` under the name the route uses, for example
   `TELEGRAM_BOT_TOKEN_INFO=123456:…`.
3. In `config.yaml`, uncomment that route's `token_env` line.
4. Send the new bot a `/start` so that the chat exists.

Then set `ALLOWED_USER_ID` in `.env` to your numeric Telegram id ([@userinfobot](https://t.me/userinfobot)
tells you). The bots ignore everyone else. Restart the server (`./run.sh` or `./deploy.sh`).

Pin the bots' chats in Telegram: the share sheet orders its row of chats by pinned and then recent,
so pinning is what keeps them in the top row. Distinct profile pictures matter more than names at
that size; [`assets/bot-icons/`](../../assets/bot-icons/) holds a set.

Three rules the daemon enforces at startup:

- Two routes may not share a token. Two pollers on one bot make both miss messages.
- A token that `config.yaml` names and `.env` does not provide, or that Telegram rejects, stops the
  daemon. A bot nobody polls would swallow everything sent to it.
- Only one process may poll a bot at a time, so stop a local copy before starting one elsewhere.

## 6. Bring the inbox to the laptop

```bash
./sync.sh
```

This copies new and edited items to `~/info-triage-inbox/<route>/`, writes `triage.md` and
`triage.org` beside them, and removes from the server the items you have deleted locally since the
last run. It uses the same `INFO_TRIAGE_SERVER` and `INFO_TRIAGE_SERVER_DIR` as `deploy.sh`; when
they are empty it reads this checkout's own `data/inbox/`. [`reviewing.md`](../reviewing.md) covers
what to do with the inbox.

`INFO_TRIAGE_INBOX` in `.env` puts the inbox somewhere else.

### Possible neighbours (optional)

After a sync, each item can be annotated with the notes it probably belongs with. It needs two
directories of notes, one of Org files and one of Markdown, and an extra set of dependencies:

```dotenv
INFO_TRIAGE_ORG_ROOT=~/notes/org
INFO_TRIAGE_OBSIDIAN_ROOT=~/notes/vault
```

```bash
uv sync --extra neighbours
```

A plain `uv sync` removes the extra again, after which every sync prints that possible neighbours
are switched off. [`../architecture/sync.md`](../architecture/sync.md#the-annotation-pass) has the
remaining settings.

## Defining routes

A route is one entry under `routes:` in `config.yaml`:

```yaml
routes:
  - name: recipes                          # lower-case letters, digits, underscores
    token_env: TELEGRAM_BOT_TOKEN_RECIPES  # optional: leave out for a route with no bot
    steps:
      - name: link-discovery
      - name: index-render
        lead_words: 120
        media_lead_words: 800
```

- The **name** becomes the inbox directory, the first half of every item handle (`recipes/2026-08-18_4`)
  and the hashtag that moves an item there (`#recipes`).
- The **first route** in the file is the default: a capture that names no route goes there.
- The **steps** run in the order listed, and removing one disables it for that route. The shipped
  `info` route lists all six; the catalogue of what each does is
  [`../architecture/preprocessing.md`](../architecture/preprocessing.md). A route that should keep
  the captured text exactly as sent needs `index-render` alone.
- `index-render` goes last, on every route: it writes the `index.md` the laptop side reads.

After changing routes, restart the server. The browser extension picks the new list up by itself, and
`sync.sh` creates a queue for whatever routes the server has.

Removing a route from the file stops captures to it. Items already in its inbox stay on disk and are
still synchronized until you have dealt with them.

## The rest of `config.yaml`

- `processing.linklist_threshold`: at how many links an item is treated as a link list, one value for
  all routes.
- `telegram.grouping`: how close together Telegram messages must be to become one item.
- `extractors.*`: retry and update settings, and optional session files for Instagram and Medium,
  unset by default. Those files are account credentials; keep them out of the repository.
- `web.port`: the HTTP port inside the container. If it changes, change the port mapping and the
  health check in `compose.yaml` to match.

Startup fails, instead of silently ignoring the problem, on an unknown field, an invalid value, a
route declared twice, a step repeated within a route, two routes sharing a token variable, or a step
out of order.

## Every setting in `.env`

| Variable | Read by | Meaning |
|---|---|---|
| `INFO_TRIAGE_CAPTURE_TOKEN` | server, capture clients | Bearer token for `POST /capture` and `GET /routes` |
| `TELEGRAM_BOT_TOKEN_<ROUTE>` | server | A route's bot token, under the name its `token_env` gives |
| `ALLOWED_USER_ID` | server | The one Telegram user the bots answer to |
| `INFO_TRIAGE_UID`, `INFO_TRIAGE_GID` | Compose | The user the container runs as; default `1000` |
| `TZ` | Compose | The container's timezone; default `UTC` |
| `INFO_TRIAGE_CONFIG` | server | A configuration file other than `./config.yaml` |
| `INFO_TRIAGE_SERVER` | `deploy.sh`, `sync.sh` | SSH destination of the server; empty means this machine |
| `INFO_TRIAGE_SERVER_DIR` | `deploy.sh`, `sync.sh` | The project directory on the server |
| `INFO_TRIAGE_DEPLOY_COMMAND` | `deploy.sh` | What rebuilds and restarts the container; default `docker compose up -d --build` |
| `INFO_TRIAGE_INBOX` | `sync.sh` | The laptop inbox; default `~/info-triage-inbox` |
| `INFO_TRIAGE_CAPTURE_URL` | `info-triage-capture` | Where the server is; default `http://localhost:8000` |
| `INFO_TRIAGE_ORG_ROOT`, `INFO_TRIAGE_OBSIDIAN_ROOT` | `sync.sh` | The two note directories of the neighbour pass; unset turns it off |
| `INFO_TRIAGE_ORG_EXCLUDE` | `sync.sh` | Org file names never searched, comma-separated |
| `INFO_TRIAGE_NEIGHBOUR_ROUTE` | `sync.sh` | The one route that is annotated; default `info` |

A variable set in the real environment overrides the file. `deploy.sh` copies `.env` to the server
as it is, so one file serves both machines.

## Optional extras

None of these is installed in the Docker image.

| Extra | Adds | Where it is useful |
|---|---|---|
| `neighbours` | the model that ranks an item's possible neighbours | the laptop that runs `sync.sh` |
| `mac-transcription` | Whisper on the Apple Silicon GPU | a Mac running extractors standalone |
| `surya` | a more accurate OCR engine for still images | a workstation; several GB |

```bash
uv sync --extra neighbours
uv sync --extra surya --extra mac-transcription
```

## Models

Voice notes and video audio are transcribed locally, and on-screen text is read locally. The default
transcription backend is `faster-whisper` with the multilingual `small` model. The Docker build
preloads the model named in `config.yaml` and the OCR model, and the container runs with downloads
disabled, so changing the model means rebuilding the image.

## Checks, when changing the code

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
bash -n deploy.sh run.sh sync.sh
docker compose config --quiet
```

Tests that exercise capture or synchronization use temporary directories. Tests that touch the
network are opt-in, behind variables such as `YOUTUBE_LIVE=1`.
