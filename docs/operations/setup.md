# Setup and configuration

Installing the project, creating the bots, and the settings the daemon reads. Deploying to a NAS is
in [`synology-deployment.md`](synology-deployment.md).

## Install

```bash
uv sync
```

Python 3.12 to 3.14. Optional extras, none of which is installed in the Docker image:

| Extra | Adds | Where it is useful |
|---|---|---|
| `neighbours` | the cross-encoder that ranks an item's possible neighbours | the laptop that runs `sync.sh` |
| `mac-transcription` | Whisper on the Apple Silicon GPU | a Mac running extractors standalone |
| `surya` | a more accurate OCR engine for still images | a workstation; several GB |

```bash
uv sync --extra neighbours                              # the laptop
uv sync --extra surya --extra mac-transcription         # a Mac workstation
```

A plain `uv sync` removes extras again. On the laptop, keep `--extra neighbours` in the command, or
every sync prints that possible neighbours are switched off.

## Credentials

Copy `.env.example` to `.env`, which Git ignores, and set every value:

```dotenv
TELEGRAM_BOT_TOKEN_INFO=your-token
TELEGRAM_BOT_TOKEN_JOB=your-token
TELEGRAM_BOT_TOKEN_CLIP=your-token
TELEGRAM_BOT_TOKEN_LANG=your-token
ALLOWED_USER_ID=your-numeric-telegram-user-id
INFO_TRIAGE_CAPTURE_TOKEN=a-long-random-string   # openssl rand -base64 32
```

`.env` holds secrets only. It is never committed and its contents are never printed.

### The four bots

Each route has its own bot. Create them with BotFather's `/newbot`, send each a `/start` so the chat
exists, and pin all four chats in Telegram: the share sheet orders its chat row by pinned and then
recent, so pinning is what puts all four in the top row. Distinct profile pictures matter more than
names at that size; [`icons/`](../../icons/) holds a set of four.

Two routes may not share a token, and a missing or rejected token stops the daemon instead of
leaving one route unpolled. Only one process may poll a bot token at a time, so stop a local copy
before starting another elsewhere.

## `config.yaml`

Non-secret daemon settings live in the commented [`config.yaml`](../../config.yaml). Relative paths
resolve from the file's own directory. Set `INFO_TRIAGE_CONFIG` in `.env` only to use another file.

- `routes[].steps` is the ordered, literal list of preprocessing for a route. Remove a step to
  disable it for that route. The steps are catalogued in
  [`preprocessing.md`](../architecture/preprocessing.md).
- `processing.linklist_threshold` is one global value.
- `telegram.grouping` controls how nearby messages are joined into one item.
- `extractors.*` holds retry and update settings, and the optional session files for Instagram and
  Medium, unset by default. Those files are account credentials: mount them as secrets.
- `web.port` is the HTTP port. If it changes, update the Compose port mapping and health check to
  match; there is no separate `PORT` variable.

Startup fails, instead of silently ignoring the problem, on an unknown field, an invalid value, a
step repeated within a route, two routes sharing a token variable, a missing route, or a step out of
order.

## Models

Voice notes and video audio are transcribed locally, and on-screen text is read locally. The default
transcription backend is `faster-whisper` with the multilingual `small` model. The Docker build
preloads the model named in `config.yaml` and the OCR model, and the container runs with downloads
disabled, so changing the model means rebuilding the image.

## Running locally

```bash
./run.sh                              # docker compose up --build
curl http://localhost:8000/health
```

The dashboard is at `http://localhost:8000/`.

## Capture clients

`POST /capture` accepts an item from anything that can send JSON with the bearer token; the contract
is in [`overview.md`](../architecture/overview.md#post-capture).

```bash
export INFO_TRIAGE_CAPTURE_URL=http://<server>:8000
export INFO_TRIAGE_CAPTURE_TOKEN=…            # from .env

info-triage-capture --route job "https://example.com/posting  worth a look"
```

The Chrome extension in [`extension/`](../../extension/) is loaded unpacked from that directory, and
takes the server address and token on its options page.

The endpoint is reachable from the whole LAN and protected only by the token. Reaching it from
elsewhere is a job for a private network such as Tailscale; see
[`tailscale-https.md`](tailscale-https.md).

## Checks

```bash
uv run pytest
uvx ruff check .
uvx ruff format --check .
bash -n deploy.sh run.sh sync.sh
docker compose config --quiet
```

Tests that exercise capture or synchronization use temporary directories. Tests that touch the
network are opt-in, behind variables such as `YOUTUBE_LIVE=1`.
