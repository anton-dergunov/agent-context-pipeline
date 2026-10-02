# Deploying to a Synology NAS

The one-time setup that lets `./deploy.sh` copy the project to a Synology NAS, rebuild the container
and check that it came up, with no password prompt and without handing the deployment account
unrestricted Docker.

The values used throughout are examples:

- NAS LAN address `192.168.1.10`, reachable as the SSH alias `server`
- deployment account `deploy`, a DSM administrator
- deployment directory `/volume1/docker/info-triage`
- application port `8000`

```text
Mac ── ./deploy.sh
        │  rsync over SSH (public-key authentication)
        ▼
NAS  /volume1/docker/info-triage
        │  sudo -n /usr/local/sbin/deploy-container info-triage   (the one passwordless command)
        ▼
     docker compose up -d --build
        │
        ▼
Mac ── health check against http://<nas>:8000/health
```

> **Scope.** SSH is for the local network only. Do not forward TCP port 22 on the router for this
> workflow, and check that UPnP has not created such a mapping.

## 1. Container Manager

In DSM, open **Package Center**, search for **Container Manager** and install it. It provides the
Docker engine and Compose. Check over SSH:

```bash
sudo docker version
sudo docker compose version
```

## 2. The deployment directory

The application lives in the `docker` shared folder:

```bash
mkdir -p /volume1/docker/info-triage
```

Not in `/volume1/@docker`: directories beginning with `@` are DSM internals. The `deploy` account
needs write access here, because `rsync` copies files into it.

## 3. SSH

**Control Panel → Terminal & SNMP → Terminal → Enable SSH service.** Port 22 is fine on the LAN. Do
not enable Telnet.

```bash
ssh deploy@192.168.1.10 whoami     # deploy
```

## 4. Public-key authentication

On the Mac, create a key if there is none (`ssh-keygen -t ed25519`) and install the public key on
the NAS as `/var/services/homes/deploy/.ssh/authorized_keys`, with `chmod 700 ~/.ssh` and
`chmod 600 ~/.ssh/authorized_keys`.

Add an alias to `~/.ssh/config` on the Mac. `deploy.sh` and `sync.sh` both use the name `server`:

```sshconfig
Host server
    HostName 192.168.1.10
    User deploy
    IdentityFile ~/.ssh/id_ed25519
```

`ssh server 'whoami'` should now print `deploy` without asking for a password.

### The post-quantum warning

A recent OpenSSH client may print a warning that the connection is not using a post-quantum key
exchange. It comes from a new client talking to the older OpenSSH server DSM ships, and is unrelated
to key authentication or to these scripts. The real fix is an OpenSSH 9.0 or newer server. Until DSM
provides one, the warning can be silenced for this alias only, which also removes it from every
`rsync` that `sync.sh` runs:

```sshconfig
Host server
    …
    WarnWeakCrypto no-pq-kex
```

This hides the warning and does not make the connection post-quantum safe. Keep it scoped to the
alias. See OpenSSH's [post-quantum guidance](https://www.openssh.com/pq.html) and the
[`WarnWeakCrypto` option](https://man.openbsd.org/ssh_config#WarnWeakCrypto).

## 5. rsync

Working SSH does not mean DSM permits rsync. Two settings are required:

1. **Control Panel → File Services → rsync**: enable the rsync service.
2. **Control Panel → Application Privileges**: allow `deploy` to use rsync. Without this, SSH
   authentication succeeds and the remotely launched `rsync --server` is still rejected.

```bash
echo info-triage >/tmp/rsync-test.txt
rsync -av /tmp/rsync-test.txt server:/volume1/docker/info-triage/
ssh server 'cat /volume1/docker/info-triage/rsync-test.txt'     # info-triage
```

The Mac and the NAS do not need the same rsync version.

## 6. The restricted deployment command

### Why passwordless SSH is not enough

`ssh server 'sudo docker ps'` passes two checks: SSH authenticates as `deploy` with the key, and
then `sudo` asks for `deploy`'s DSM password. So `ssh server 'whoami'` works unattended while
`ssh server 'sudo -n docker ps'` fails with `sudo: a password is required`.

Do not solve this with `deploy ALL=(ALL) NOPASSWD: ALL`, and do not grant passwordless
`/usr/local/bin/docker` either: control of Docker is equivalent to root. Expose one specific command
instead.

### The wrapper

As root on the NAS (`sudo -i`), check Docker's path with `command -v docker`, then create
`/usr/local/sbin/deploy-container`:

```sh
#!/bin/sh

set -eu

case "${1:-}" in
    info-triage)
        PROJECT_DIR="/volume1/docker/info-triage"
        ;;
    *)
        echo "Unknown project: ${1:-}" >&2
        exit 1
        ;;
esac

DOCKER="/usr/local/bin/docker"
COMPOSE="$PROJECT_DIR/compose.yaml"

echo "==> Building and starting $1"

"$DOCKER" compose \
    -f "$COMPOSE" \
    --project-directory "$PROJECT_DIR" \
    up -d --build

echo "==> Status"

"$DOCKER" compose \
    -f "$COMPOSE" \
    --project-directory "$PROJECT_DIR" \
    ps
```

```bash
chown root:root /usr/local/sbin/deploy-container
chmod 755 /usr/local/sbin/deploy-container
```

The script accepts only the one named deployment.

### The sudo rule

DSM's `/etc/sudoers` ends with `#includedir /etc/sudoers.d`, which is a directive despite the `#`.
Use a drop-in file and leave the main file as DSM ships it. Keep a root session open while testing.

Create `/etc/sudoers.d/deploy-container` containing exactly:

```sudoers
deploy ALL=(root) NOPASSWD: /usr/local/sbin/deploy-container info-triage
```

```bash
chown root:root /etc/sudoers.d/deploy-container
chmod 440 /etc/sudoers.d/deploy-container
visudo -c
```

From a separate terminal on the Mac:

```bash
ssh server 'sudo -n /usr/local/sbin/deploy-container info-triage'   # works, no password
ssh server 'sudo -n /usr/local/bin/docker ps'                       # sudo: a password is required
```

That difference is intended.

## 7. Deploying

The Mac holds the source of truth. The NAS copy is a deployment target.

```bash
./run.sh                               # local test: docker compose up --build
curl http://localhost:8000/health

./deploy.sh
```

[`deploy.sh`](../../deploy.sh) stops at the first failing command. It:

1. creates `data/staging/<route>/` and `data/inbox/<route>/` for every route;
2. mirrors the project with `rsync -az --delete`, excluding `.git/`, `.env`, `data/`, `logs/`,
   `.venv/`, `__pycache__/` and `.DS_Store`;
3. copies `.env` separately and sets its mode to `600`;
4. runs the one passwordless command, which rebuilds and restarts the container;
5. polls `GET /health` from the Mac for up to 60 seconds, taking the address from the `server`
   alias, and reports success only when the service answers.

`--delete` makes the remote tree mirror the local one: anything present remotely and absent locally
is removed unless excluded. That is why `data/` is excluded. **A deployment preserves the remote
`data/` directory.** The one exception is a schema change the store refuses to migrate, such as the
move to routes, after which `data/` has to be deleted by hand.

Only one process may poll a Telegram bot token at a time. Stop any locally running copy before
deploying.

### The container

[`compose.yaml`](../../compose.yaml) and the [`Dockerfile`](../../Dockerfile) are the reference;
what matters about them:

- The container runs as user and group `1026:100`, so that deletions driven from the laptop can
  remove item directories the container created.
- `./data` is mounted at `/app/data`, and port 8000 is published to the LAN.
- The image preloads and validates the OCR model and the Whisper model named in `config.yaml`, and
  runs with model downloads disabled. Changing the transcription model means rebuilding the image.
- The PyTorch-based OCR engine and the Apple Silicon transcription backend are workstation extras
  and are not in the image. Neither is the `neighbours` extra.
- If `web.port` changes in `config.yaml`, the Compose port mapping and health-check URL change with
  it. The daemon reads no separate `PORT` variable.

### Resource limits

A Dockerfile cannot set CPU or memory limits. They are runtime settings and live in `compose.yaml`.

- **Memory.** `mem_limit: 8g` with `memswap_limit` equal to it. This is a ceiling, not a
  reservation, and a guard against a runaway process, not a fit: the measured peak for Whisper
  `small` is about 1.3 GB, and OCR and transcription models are loaded one after the other so their
  peaks do not add. On a NAS with 20 GB it leaves about 12 GB for DSM, the file cache and other
  containers.
- **CPU.** `cpu_shares: 512` lowers the container's priority under contention. It is not a hard
  limit. Compose `cpus` cannot be used: the Synology kernel does not expose the scheduler support
  Docker needs for a CPU quota.
- **Threads.** This matters more than the CPU setting. ONNX Runtime, CTranslate2 and OpenMP size
  their thread pools from the host's CPU count and ignore the container's limits, so the image pins
  OCR and transcription to one thread each.

## 8. Monitoring

In DSM, **Container Manager → Container** shows the `info-triage` container's state, CPU and memory
use, logs and port mappings. The dashboard at `http://192.168.1.10:8000/` shows what the application
itself is doing.

From a shell on the NAS, with normal sudo authentication:

```bash
sudo docker compose \
    -f /volume1/docker/info-triage/compose.yaml \
    --project-directory /volume1/docker/info-triage \
    logs -f --tail=100
```

`ps` in place of `logs …` shows the status. The passwordless rule deliberately does not cover these.
If unattended monitoring is wanted later, add a second narrowly scoped root-owned command.

## 9. Checklist

| Check | Expected |
|---|---|
| `ssh server 'whoami'` | `deploy`, no password prompt |
| `rsync -av /tmp/rsync-test.txt server:/volume1/docker/info-triage/` | succeeds, no password |
| `ssh server 'sudo -n /usr/local/sbin/deploy-container info-triage'` | succeeds, no password |
| `ssh server 'sudo -n /usr/local/bin/docker ps'` | fails: a password is required |
| `curl http://192.168.1.10:8000/health` | `Info Triage is running` |
| router port forwards and UPnP mappings | none for TCP 22 |

## 10. What this does not protect against

The wrapper limits direct passwordless Docker, but it is **not a complete privilege boundary**.
`deploy` can `rsync` a new `compose.yaml` or `Dockerfile` into the deployment directory, and the
root-owned command then builds and runs whatever they define. Someone who gained control of the
`deploy` account could use that to run anything.

A stronger design keeps the Compose and Docker definitions root-controlled and lets `deploy` update
only application source, staged in a separate directory that a restricted root step copies from. For
a single-user home NAS that is a second hardening step. Until it is done, treat the `deploy` account
as able to become root on the NAS.

For HTTPS access from outside the LAN, see [`tailscale-https.md`](tailscale-https.md).
