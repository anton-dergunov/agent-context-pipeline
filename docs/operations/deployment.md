# Deploying to a server

`./deploy.sh` copies this checkout to a server over SSH, rebuilds the container there and waits for
it to answer. The checkout on your laptop is the source of truth; the copy on the server is a
deployment target that you never edit by hand.

```text
laptop ── ./deploy.sh
            │  rsync over SSH
            ▼
server   <INFO_TRIAGE_SERVER_DIR>
            │  docker compose up -d --build     (or INFO_TRIAGE_DEPLOY_COMMAND)
            ▼
laptop ── health check against http://<server>:8000/health
```

When the server is the machine you are on, leave `INFO_TRIAGE_SERVER` empty: the script then builds
and starts the container in place, and the rest of this page does not apply.

## What the server needs

- **Docker with Compose.** `docker compose version` should answer.
- **SSH access with a key**, so that nothing prompts for a password. An alias in `~/.ssh/config` on
  the laptop keeps the address, user and key in one place:

  ```sshconfig
  Host my-server
      HostName 192.168.1.10
      User deploy
      IdentityFile ~/.ssh/id_ed25519
  ```

- **`rsync`** on both machines. They do not need the same version.
- **A user allowed to run Docker**, or one specific command it may run as root; see below.
- **A project directory** that user can write to, on a disk with room for the image (about 3 GB) and
  for what you capture.

## Settings

In `.env`:

```dotenv
# An alias from ~/.ssh/config, or user@host.
INFO_TRIAGE_SERVER=my-server
# Absolute path of the project directory on the server. Its data/ holds everything captured.
INFO_TRIAGE_SERVER_DIR=/srv/info-triage
# The user that owns that directory there: `id -u` and `id -g` on the server.
INFO_TRIAGE_UID=1000
INFO_TRIAGE_GID=1000
TZ=Europe/London
```

The container runs as `INFO_TRIAGE_UID:INFO_TRIAGE_GID`. Use the deploying user's ids: `sync.sh`
removes processed items over SSH as that user, and it can only remove what that user owns.

### When the user may not run Docker

By default the script runs `docker compose up -d --build` in the project directory, which needs a
user in the `docker` group. Membership of that group is equivalent to root. Where that is not
acceptable, give the user one root-owned command instead and name it:

```dotenv
INFO_TRIAGE_DEPLOY_COMMAND=sudo -n /usr/local/sbin/deploy-container info-triage
```

[`synology.md`](synology.md#the-restricted-deployment-command) shows such a wrapper and its sudoers
rule; the same pattern works on any Linux.

## What `deploy.sh` does

It stops at the first failing command.

1. Checks that `.env` and `config.yaml` exist locally.
2. Creates `data/` in the project directory on the server.
3. Mirrors the checkout with `rsync -az --delete`, leaving out `.git/`, `.env`, `data/`, the virtual
   environment, caches and local extractor output.
4. Copies `.env` separately and sets its mode to `600`.
5. Runs the deploy command, which rebuilds the image and restarts the container.
6. Polls `GET /health` from the laptop for up to 60 seconds and reports success only when the
   service answers. The address is the alias's `HostName`.

`--delete` makes the server's tree mirror the local one: anything there and not here is removed,
unless it is excluded. That is why `data/` is excluded. **A deployment preserves the server's
`data/` directory**, and with it every captured item.

Only one process may poll a Telegram bot at a time. Stop a locally running copy before deploying.

## The container

[`compose.yaml`](../../compose.yaml) and the [`Dockerfile`](../../Dockerfile) are the reference; what
matters about them:

- `./data` is mounted at `/app/data`, and port 8000 is published to the local network.
- The image preloads and validates the OCR model and the Whisper model named in `config.yaml`, and
  runs with model downloads disabled. Changing the transcription model means rebuilding the image.
- The PyTorch-based OCR engine, the Apple Silicon transcription backend and the `neighbours` extra
  are workstation extras and are not in the image.
- If `web.port` changes in `config.yaml`, the Compose port mapping and health-check URL change with
  it. The daemon reads no separate `PORT` variable.

### Resource limits

They are runtime settings and live in `compose.yaml`.

- **Memory.** `mem_limit: 8g` with `memswap_limit` equal to it. This is a ceiling, not a
  reservation, and a guard against a runaway process: the measured peak for Whisper `small` is about
  1.3 GB, and OCR and transcription models are loaded one after the other so their peaks do not
  add. Lower it on a small machine.
- **CPU.** `cpu_shares: 512` lowers the container's priority under contention. It is not a hard
  limit.
- **Threads.** This matters more than the CPU setting. ONNX Runtime, CTranslate2 and OpenMP size
  their thread pools from the host's CPU count and ignore the container's limits, so the image pins
  OCR and transcription to one thread each.

## Looking at it

The dashboard at `http://<server>:8000/` shows what the application is doing. For the container
itself, on the server:

```bash
docker compose --project-directory /srv/info-triage ps
docker compose --project-directory /srv/info-triage logs -f --tail=100
```

`data/logs/processor-runs.jsonl` records every processing step, with the exception and traceback of
any that failed.

## Access

The port is published to the whole local network. The dashboard is unauthenticated and read-only;
captures need the bearer token, which is the only access control. Do not forward the port on your
router. To reach the server from elsewhere, and to get HTTPS, use a private network:
[`tailscale-https.md`](tailscale-https.md).
