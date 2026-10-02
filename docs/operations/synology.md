# Deploying to a Synology NAS

What a Synology needs on top of [`deployment.md`](deployment.md): DSM's own switches for SSH and
rsync, and a way to rebuild the container with no password prompt and without handing the
deployment account unrestricted Docker.

The values used throughout are examples:

- NAS LAN address `192.168.1.10`, reachable as the SSH alias `nas`
- deployment account `deploy`, a DSM administrator
- deployment directory `/volume1/docker/info-triage`
- application port `8000`

They go into `.env` like this:

```dotenv
INFO_TRIAGE_SERVER=nas
INFO_TRIAGE_SERVER_DIR=/volume1/docker/info-triage
INFO_TRIAGE_DEPLOY_COMMAND=sudo -n /usr/local/sbin/deploy-container info-triage
# The first user DSM creates is 1026, in the group users (100): check with `id` on the NAS.
INFO_TRIAGE_UID=1026
INFO_TRIAGE_GID=100
```

> **Scope.** SSH is for the local network only. Do not forward TCP port 22 on the router for this
> workflow, and check that UPnP has not created such a mapping.

## Container Manager

In DSM, open **Package Center**, search for **Container Manager** and install it. It provides the
Docker engine and Compose. Check over SSH:

```bash
sudo docker version
sudo docker compose version
```

## The deployment directory

The application lives in the `docker` shared folder:

```bash
mkdir -p /volume1/docker/info-triage
```

Not in `/volume1/@docker`: directories beginning with `@` are DSM internals. The `deploy` account
needs write access here, because `rsync` copies files into it.

## SSH

**Control Panel → Terminal & SNMP → Terminal → Enable SSH service.** Port 22 is fine on the LAN. Do
not enable Telnet.

```bash
ssh deploy@192.168.1.10 whoami     # deploy
```

## Public-key authentication

On the Mac, create a key if there is none (`ssh-keygen -t ed25519`) and install the public key on
the NAS as `/var/services/homes/deploy/.ssh/authorized_keys`, with `chmod 700 ~/.ssh` and
`chmod 600 ~/.ssh/authorized_keys`.

Add an alias to `~/.ssh/config` on the Mac, and name it in `.env` as `INFO_TRIAGE_SERVER`:

```sshconfig
Host nas
    HostName 192.168.1.10
    User deploy
    IdentityFile ~/.ssh/id_ed25519
```

`ssh nas 'whoami'` should now print `deploy` without asking for a password.

### The post-quantum warning

A recent OpenSSH client may print a warning that the connection is not using a post-quantum key
exchange. It comes from a new client talking to the older OpenSSH server DSM ships, and is unrelated
to key authentication or to these scripts. The real fix is an OpenSSH 9.0 or newer server. Until DSM
provides one, the warning can be silenced for this alias only, which also removes it from every
`rsync` that `sync.sh` runs:

```sshconfig
Host nas
    …
    WarnWeakCrypto no-pq-kex
```

This hides the warning and does not make the connection post-quantum safe. Keep it scoped to the
alias. See OpenSSH's [post-quantum guidance](https://www.openssh.com/pq.html) and the
[`WarnWeakCrypto` option](https://man.openbsd.org/ssh_config#WarnWeakCrypto).

## rsync

Working SSH does not mean DSM permits rsync. Two settings are required:

1. **Control Panel → File Services → rsync**: enable the rsync service.
2. **Control Panel → Application Privileges**: allow `deploy` to use rsync. Without this, SSH
   authentication succeeds and the remotely launched `rsync --server` is still rejected.

```bash
echo info-triage >/tmp/rsync-test.txt
rsync -av /tmp/rsync-test.txt nas:/volume1/docker/info-triage/
ssh nas 'cat /volume1/docker/info-triage/rsync-test.txt'     # info-triage
```

The Mac and the NAS do not need the same rsync version.

## The restricted deployment command

### Why passwordless SSH is not enough

`ssh nas 'sudo docker ps'` passes two checks: SSH authenticates as `deploy` with the key, and
then `sudo` asks for `deploy`'s DSM password. So `ssh nas 'whoami'` works unattended while
`ssh nas 'sudo -n docker ps'` fails with `sudo: a password is required`.

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
ssh nas 'sudo -n /usr/local/sbin/deploy-container info-triage'   # works, no password
ssh nas 'sudo -n /usr/local/bin/docker ps'                       # sudo: a password is required
```

That difference is intended.

## Deploying

With the settings above in `.env`:

```bash
./deploy.sh
```

[`deployment.md`](deployment.md#what-deploysh-does) lists what it does. Two things are particular to
a Synology:

- The container runs as `1026:100`, the account that owns the deployment directory, so that
  `sync.sh` can remove processed items over SSH.
- Compose's `cpus` setting cannot be used: the Synology kernel does not expose the scheduler support
  Docker needs for a CPU quota. `compose.yaml` uses `cpu_shares` instead, which lowers priority
  without a hard limit.

## Monitoring

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

## Checklist

| Check | Expected |
|---|---|
| `ssh nas 'whoami'` | `deploy`, no password prompt |
| `rsync -av /tmp/rsync-test.txt nas:/volume1/docker/info-triage/` | succeeds, no password |
| `ssh nas 'sudo -n /usr/local/sbin/deploy-container info-triage'` | succeeds, no password |
| `ssh nas 'sudo -n /usr/local/bin/docker ps'` | fails: a password is required |
| `curl http://192.168.1.10:8000/health` | `Info Triage is running` |
| router port forwards and UPnP mappings | none for TCP 22 |

## What this does not protect against

The wrapper limits direct passwordless Docker, but it is **not a complete privilege boundary**.
`deploy` can `rsync` a new `compose.yaml` or `Dockerfile` into the deployment directory, and the
root-owned command then builds and runs whatever they define. Someone who gained control of the
`deploy` account could use that to run anything.

A stronger design keeps the Compose and Docker definitions root-controlled and lets `deploy` update
only application source, staged in a separate directory that a restricted root step copies from. For
a single-user home NAS that is a second hardening step. Until it is done, treat the `deploy` account
as able to become root on the NAS.

For HTTPS access from outside the LAN, see [`tailscale-https.md`](tailscale-https.md).
