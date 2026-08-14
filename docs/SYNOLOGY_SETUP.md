# Synology NAS Docker Deployment Setup

This document describes the complete setup for developing and testing a
Dockerized application on a Mac, then deploying it explicitly to a
Synology NAS with one command.

The concrete setup documented here is:

-   Synology NAS running DSM and Container Manager
-   NAS hostname: `server`
-   NAS LAN IP: `192.168.1.10`
-   DSM/SSH deployment user: `deploy`
-   Mac SSH alias: `server`
-   NAS deployment directory: `/volume1/docker/info-triage`
-   Application port: `8000`
-   SSH public-key authentication
-   rsync-over-SSH for copying application files
-   a narrowly scoped passwordless `sudo` command for deployment
-   Docker Compose for build/start/restart
-   a health check after deployment

The goal is this workflow:

``` text
Mac / VS Code
    |
    | develop and test locally
    v
docker compose up --build
    |
    | explicit deployment: ./deploy.sh
    v
rsync over SSH
    |
    v
Synology NAS
    |
    v
restricted sudo deployment command
    |
    v
docker compose up -d --build
    |
    v
health check from Mac
```

> **Security scope:** SSH should be accessible from the local network
> only. Do not configure router port forwarding for TCP port 22 merely
> for this deployment workflow.

## Migrating the existing prototype

The original prototype used the project name and directory `hello`. Before the
first Info Triage deployment, make this one-time change from a NAS root shell.

First stop the old container and rename the deployment directory:

```bash
/usr/local/bin/docker compose \
    -f /volume1/docker/hello/compose.yaml \
    --project-directory /volume1/docker/hello \
    down

mv /volume1/docker/hello /volume1/docker/info-triage
```

Then edit `/usr/local/sbin/deploy-container` so its only accepted case is:

```sh
info-triage)
    PROJECT_DIR="/volume1/docker/info-triage"
    ;;
```

Finally, replace the command in `/etc/sudoers.d/deploy-container` with:

```sudoers
deploy ALL=(root) NOPASSWD: /usr/local/sbin/deploy-container info-triage
```

Keep the existing ownership and modes (`root:root`, mode `755` for the wrapper
and `440` for the sudoers file), then validate the sudoers configuration before
closing the root session:

```bash
visudo -c
```

This migration is required only once. Normal deployments afterwards use
`./deploy.sh`.

------------------------------------------------------------------------

## 1. Install Container Manager on the Synology

In DSM:

1.  Open **Package Center**.
2.  Search for **Container Manager**.
3.  Install it.

Container Manager provides the Docker engine and Docker Compose support
used below.

After installation, SSH access to the NAS can be used to run commands
such as:

``` bash
sudo docker version
sudo docker compose version
```

------------------------------------------------------------------------

## 2. Create or use the `docker` shared folder

This setup uses the Synology shared folder:

``` text
/volume1/docker
```

In DSM this appears as the `docker` shared folder.

Do not confuse it with Synology's internal directory:

``` text
/volume1/@docker
```

Directories beginning with `@` are DSM/application internals and should
not be used for application source.

The application is deployed to:

``` text
/volume1/docker/info-triage
```

Create it if necessary:

``` bash
mkdir -p /volume1/docker/info-triage
```

The deployment user `deploy` needs write access to this directory
because `rsync` copies files into it.

------------------------------------------------------------------------

## 3. Enable SSH in DSM

Open:

**Control Panel → Terminal & SNMP → Terminal**

Enable:

``` text
Enable SSH service
```

Port `22` is fine for LAN-only access.

Do not enable Telnet.

### Router security

Do **not** create a router/NAT port-forward such as:

``` text
WAN TCP 22 → 192.168.1.10 TCP 22
```

Also check that UPnP has not automatically created an SSH mapping.

The intended network topology is:

``` text
Internet
    |
 router/firewall
    |       no TCP/22 forwarding
    |
home LAN
    |
    +---- Mac
    |
    +---- Synology 192.168.1.10:22
```

------------------------------------------------------------------------

## 4. Use the administrator account for deployment

The SSH/deployment account used here is:

``` text
deploy
```

It is a DSM administrator account.

Test initial SSH access from the Mac:

``` bash
ssh deploy@192.168.1.10
```

On the NAS:

``` bash
whoami
```

Expected:

``` text
deploy
```

Administrative Docker commands normally require `sudo`.

------------------------------------------------------------------------

## 5. Configure SSH public-key authentication

On the Mac, check whether an Ed25519 key already exists:

``` bash
ls ~/.ssh/*.pub
```

If necessary, create one:

``` bash
ssh-keygen -t ed25519
```

The default private/public key pair is normally:

``` text
~/.ssh/id_ed25519
~/.ssh/id_ed25519.pub
```

Install the public key for `deploy` on the NAS. The resulting NAS file
should be:

``` text
/var/services/homes/deploy/.ssh/authorized_keys
```

Typical permissions are:

``` bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

Configure a convenient alias on the Mac in:

``` text
~/.ssh/config
```

For example:

``` sshconfig
Host server
    HostName 192.168.1.10
    User deploy
    IdentityFile ~/.ssh/id_ed25519
```

Test:

``` bash
ssh server
```

This should log in as `deploy` without asking for the DSM account
password.

Verify:

``` bash
ssh server 'whoami'
```

Expected:

``` text
deploy
```

### Post-quantum warning

A recent macOS/OpenSSH client may print:

``` text
WARNING: connection is not using a post-quantum key exchange algorithm.
This session may be vulnerable to "store now, decrypt later" attacks.
The server may need to be upgraded.
```

This is caused by the newer SSH client connecting to the older OpenSSH
server shipped by DSM. It is independent of SSH public-key
authentication and of the deployment scripts described here.

The cryptographic fix is to upgrade the NAS SSH server to OpenSSH 9.0 or
newer, which supports a post-quantum key exchange. Until DSM provides such an
upgrade, the warning can be suppressed only for this LAN-only NAS alias by
adding the following line to the existing `Host server` block in
`~/.ssh/config`:

See OpenSSH's [post-quantum guidance](https://www.openssh.com/pq.html) and the
[`WarnWeakCrypto` client option](https://man.openbsd.org/ssh_config#WarnWeakCrypto).

``` sshconfig
Host server
    HostName 192.168.1.10
    User deploy
    IdentityFile ~/.ssh/id_ed25519
    WarnWeakCrypto no-pq-kex
```

This setting is used by direct `ssh` commands and by rsync's SSH connections,
so it removes the repeated warnings from `sync.sh`. It only hides the warning;
it does not make the connection post-quantum-safe. Keep it scoped to `server`
rather than disabling the warning globally.

------------------------------------------------------------------------

## 6. Enable rsync in DSM

SSH working does not automatically mean Synology will permit rsync.

Two DSM settings are required.

### 6.1 Enable the rsync service

Open:

**Control Panel → File Services → rsync**

Enable the rsync service and apply the setting.

### 6.2 Give `deploy` application privilege for rsync

Open:

**Control Panel → Application Privileges**

Find the rsync application/service and allow:

``` text
deploy
```

Without this permission, SSH authentication can succeed but the remotely
launched `rsync --server ...` command can still be rejected by DSM.

### 6.3 Test rsync

On the Mac:

``` bash
echo info-triage >/tmp/rsync-test.txt

rsync -av \
    /tmp/rsync-test.txt \
    server:/volume1/docker/info-triage/
```

Successful output should resemble:

``` text
sending incremental file list
rsync-test.txt
```

Verify:

``` bash
ssh server 'cat /volume1/docker/info-triage/rsync-test.txt'
```

Expected:

``` text
info-triage
```

The Mac and NAS do not need identical rsync versions. For example, rsync
3.4.x on the Mac can interoperate with the older rsync 3.1.x supplied by
DSM.

------------------------------------------------------------------------

## 7. Why ordinary passwordless SSH is not enough

A command such as:

``` bash
ssh server 'sudo docker ps'
```

has two authentication layers:

``` text
SSH authentication
        |
        +-- SSH key authenticates as deploy
        |
        v
sudo authentication
        |
        +-- normally asks for deploy's DSM password
```

Therefore this can work without a password:

``` bash
ssh server 'whoami'
```

while this fails:

``` bash
ssh server 'sudo -n docker ps'
```

with:

``` text
sudo: a password is required
```

The `-n` option deliberately tells `sudo` not to prompt interactively.

Do **not** solve this by granting:

``` text
deploy ALL=(ALL) NOPASSWD: ALL
```

Also avoid granting unrestricted passwordless Docker access such as:

``` text
deploy ALL=(root) NOPASSWD: /usr/local/bin/docker
```

Docker control is effectively root-equivalent.

Instead, expose one specific deployment command.

------------------------------------------------------------------------

## 8. Create the restricted deployment command

Create the conventional local administrative directory if DSM does not
already have it:

``` bash
sudo mkdir -p /usr/local/sbin
```

Create:

``` text
/usr/local/sbin/deploy-container
```

First check Docker's absolute path:

``` bash
command -v docker
```

The script below assumes:

``` text
/usr/local/bin/docker
```

Use the actual path returned by the NAS if it differs.

Create/edit the script as root:

``` bash
sudo -i
vim /usr/local/sbin/deploy-container
```

Contents:

``` sh
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

Set ownership and permissions:

``` bash
chown root:root /usr/local/sbin/deploy-container
chmod 755 /usr/local/sbin/deploy-container
```

Verify:

``` bash
ls -l /usr/local/sbin/deploy-container
```

It should be owned by `root`.

Test it while root:

``` bash
/usr/local/sbin/deploy-container info-triage
```

The script deliberately accepts only the named `info-triage` deployment.

------------------------------------------------------------------------

## 9. Configure the restricted sudo rule

DSM's `/etc/sudoers` includes:

``` text
#includedir /etc/sudoers.d
```

Use a separate drop-in instead of putting custom deployment rules
directly into the main Synology sudoers file.

> On sudoers systems, `#includedir` is a sudoers directive despite
> beginning with `#`.

Become root and keep this root session open while testing changes:

``` bash
sudo -i
```

Create:

``` text
/etc/sudoers.d/deploy-container
```

with:

``` bash
vim /etc/sudoers.d/deploy-container
```

Contents:

``` sudoers
deploy ALL=(root) NOPASSWD: /usr/local/sbin/deploy-container info-triage
```

Set secure permissions:

``` bash
chown root:root /etc/sudoers.d/deploy-container
chmod 440 /etc/sudoers.d/deploy-container
```

Verify:

``` bash
cat /etc/sudoers.d/deploy-container
ls -l /etc/sudoers.d/deploy-container
```

The final custom file should contain exactly:

``` text
deploy ALL=(root) NOPASSWD: /usr/local/sbin/deploy-container info-triage
```

The main `/etc/sudoers` remains DSM's standard configuration:

``` text
## sudoers file.

# Enable logging of a command's output.
# Use sudoreplay to play back logged sessions.
Defaults syslog=authpriv

# Allow root to execute any command
root ALL=(ALL) ALL

# Allow members of group administrators to execute any command
%administrators ALL=(ALL) ALL

# Configure privilege of wheel group
Cmnd_Alias SHELL = /bin/ash, /bin/sh, /bin/bash
Cmnd_Alias SU = /usr/bin/su
%wheel ALL=(ALL) NOPASSWD: ALL, !SHELL, !SU

# Include user-defined sudoers
#includedir /etc/sudoers.d
```

### Test the restricted rule

From a separate Mac terminal:

``` bash
ssh server 'sudo -n /usr/local/sbin/deploy-container info-triage'
```

This should work without asking for a password.

By contrast:

``` bash
ssh server 'sudo -n /usr/local/bin/docker ps'
```

should fail with:

``` text
sudo: a password is required
```

That distinction is intentional.

------------------------------------------------------------------------

## 10. Local project structure on the Mac

The source of truth is the project on the Mac/GitHub, not the copy on
the NAS.

A minimal project is:

``` text
info-triage/
├── app.py
├── compose.yaml
├── Dockerfile
├── deploy.sh
├── pyproject.toml
├── uv.lock
├── info_triage/
├── docker/
├── run.sh
└── sync.sh
```

The Mac is used for:

-   VS Code development
-   local Docker testing
-   Git commits
-   GitHub
-   explicit deployment

The NAS copy is a deployment target.

------------------------------------------------------------------------

## 11. Info Triage application

`app.py` runs the Telegram polling bot and a small HTTP health server in one
process. It stores durable application data below `/app/data` and exposes
`GET /health` on port 8000.

Those non-secret values, Telegram grouping timing, and the ordered processing
steps come from the repository's commented `config.yaml`. The container uses
that file by default. `.env` contains the Telegram bot token and allowed user
ID; `INFO_TRIAGE_CONFIG` is available only when an alternate configuration file
is deliberately mounted into the container.

With the shipped configuration, the server binds to:

``` text
0.0.0.0:8000
```

rather than `127.0.0.1`, so Docker can expose it outside the container. If
`web.port` changes, update the Compose port mapping and health-check URL to the
same container port; YAML remains authoritative for the daemon's listener.

------------------------------------------------------------------------

## 12. Dockerfile

The repository Dockerfile is a multi-stage Python 3.12 build. It installs the
single locked uv project, then reads `config.yaml` and preloads and validates
the reviewed portable RapidOCR and configured `faster-whisper` model. The
shipped configuration selects multilingual `small`. The runtime stage
contains `curl` for the health check and `libgomp1` for ONNX Runtime, operates
with network model loading disabled, runs as `1026:100`, and still starts
`python app.py`.

Surya/PyTorch and Apple-Silicon MLX remain optional workstation dependencies;
they are deliberately absent from the Synology Linux image.
Changing the configured transcription model requires rebuilding the image so
the replacement model is present before offline runtime begins.

------------------------------------------------------------------------

## 13. Docker Compose configuration

`compose.yaml`:

``` yaml
services:
  info-triage:
    build: .
    container_name: info-triage
    restart: unless-stopped
    user: "1026:100"
    mem_limit: 8g
    memswap_limit: 8g
    cpu_shares: 512

    env_file:
      - .env

    environment:
      INSTAGRAM_OCR_THREADS: "1"
      TZ: Europe/London

    volumes:
      - ./data:/app/data

    ports:
      - "8000:8000"

    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 5s
```

`cpu_shares` lowers the container's relative CPU priority during contention;
it is not a hard CPU limit. Do not add Compose `cpus` on this Synology: its
kernel does not expose the CFS scheduler support Docker needs for `NanoCPUs`.
The OCR environment setting and configured one-thread voice processor bound the
CPU-heavy library work.
The 8 GB memory limit is a ceiling, not a reservation, and leaves roughly 12 GB
of the upgraded NAS's 20 GB for DSM, filesystem cache, and other containers.
Keeping `memswap_limit` equal to `mem_limit` prevents additional swap usage.

The mapping:

``` text
8000:8000
```

means:

``` text
NAS TCP 8000 → container TCP 8000
```

The application can therefore be reached on the LAN at:

``` text
http://192.168.1.10:8000
```

This address displays the read-only processing dashboard. The container health
check continues to use `http://localhost:8000/health`.

`restart: unless-stopped` tells Docker to bring the container back after
Docker/NAS restarts unless it was explicitly stopped.

------------------------------------------------------------------------

## 14. Test locally on the Mac

`run.sh`:

``` bash
#!/usr/bin/env bash

set -euo pipefail

docker compose up --build
```

Make it executable:

``` bash
chmod +x run.sh
```

Run:

``` bash
./run.sh
```

Test:

``` bash
curl http://localhost:8000/health
```

Expected:

``` text
Info Triage is running
```

Stop the foreground Compose process with `Ctrl-C`.

This local test is intentionally separate from deployment.

------------------------------------------------------------------------

## 15. One-command deployment from the Mac

`deploy.sh`:

``` bash
#!/usr/bin/env bash

set -euo pipefail

REMOTE="server"
REMOTE_DIR="/volume1/docker/info-triage"
URL="http://192.168.1.10:8000/health"

if [[ ! -f .env ]]; then
    echo "Missing .env with Telegram credentials" >&2
    exit 1
fi

echo "==> Preparing deployment directory"

ssh "$REMOTE" \
    "mkdir -p '$REMOTE_DIR/data/staging' '$REMOTE_DIR/data/inbox'"

echo "==> Copying files to NAS"

rsync -az --delete \
    --exclude '.git/' \
    --exclude '.env' \
    --exclude 'data/' \
    --exclude 'logs/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    ./ "${REMOTE}:${REMOTE_DIR}/"

rsync -az .env "${REMOTE}:${REMOTE_DIR}/.env"
ssh "$REMOTE" "chmod 600 '$REMOTE_DIR/.env'"

echo "==> Building and restarting container"

ssh "$REMOTE" \
    'sudo -n /usr/local/sbin/deploy-container info-triage'

echo "==> Waiting for service"

echo "==> Health check"

curl --fail --silent --show-error \
    --connect-timeout 2 \
    --max-time 5 \
    --retry 60 \
    --retry-all-errors \
    --retry-delay 1 \
    --retry-max-time 60 \
    "$URL"

echo
echo "==> Deployment complete"
```

Make it executable:

``` bash
chmod +x deploy.sh
```

Deploy:

``` bash
./deploy.sh
```

The script performs:

1.  `rsync` of the Mac project to the NAS.
2.  SSH public-key authentication as `deploy`.
3.  Passwordless execution of only `deploy-container info-triage`.
4.  `docker compose up -d --build` as root.
5.  Display of Compose container status.
6.  A health check from the Mac that retries connection failures, empty
    replies, timeouts, and unsuccessful HTTP responses for up to 60 seconds.
7.  Confirmation that the deployed service is reachable before reporting a
    successful deployment.

Because of:

``` bash
set -euo pipefail
```

the script stops when an important command fails rather than continuing
and claiming a successful deployment.

------------------------------------------------------------------------

## 16. What `rsync --delete` means

The deployment uses:

``` bash
rsync -az --delete
```

This makes the remote deployed tree closely mirror the local source
tree.

Anything present remotely but absent locally can be deleted unless
excluded.

The exclusions therefore matter:

``` text
.git/
.env
data/
logs/
.venv/
__pycache__/
.DS_Store
```

The main mirror operation excludes `.env`, then `deploy.sh` copies that file
separately and sets mode `600`. It contains the Telegram bot token and must not
be committed to Git.

Persistent runtime data should similarly live in an excluded directory
such as:

``` text
data/
```

------------------------------------------------------------------------

### Laptop inbox synchronization

Run this from the project on the laptop whenever you want to synchronize:

```bash
./sync.sh
```

New and edited NAS items are copied to `~/info-triage-inbox/`. The script keeps
only each delivered item ID and revision in
`~/.local/state/info-triage/delivered-items`; it does not use a hidden content
snapshot for deletion tracking. Existing one-column manifests are migrated
automatically.

After a successful sync, the command atomically regenerates
`~/info-triage-inbox/inbox.md`. It is an oldest-first Markdown view containing
the capture time, user-facing metadata, a link to each item directory, and the
message body. It is overwritten on every sync and should not be edited as a way
to acknowledge items.

Moving or deleting a delivered item directory from the laptop inbox marks its
current revision processed. The next sync deletes the matching NAS directory.
If a constituent Telegram message is subsequently edited, the server creates a
higher revision and the next sync restores it to the laptop.

If the delivered-items file exists but the laptop inbox directory is missing,
the script aborts instead of interpreting the missing directory as a request to
delete every delivered NAS item.

------------------------------------------------------------------------

## 17. Monitoring

### DSM GUI

Open:

**DSM → Container Manager → Container**

The `info-triage` container can be inspected there for:

-   running/stopped state
-   CPU usage
-   memory usage
-   logs
-   port mappings
-   start/stop/restart operations

### CLI status

Interactive administrative inspection can be done after SSH login:

``` bash
ssh server
sudo docker compose \
    -f /volume1/docker/info-triage/compose.yaml \
    --project-directory /volume1/docker/info-triage \
    ps
```

### Logs

On the NAS:

``` bash
sudo docker compose \
    -f /volume1/docker/info-triage/compose.yaml \
    --project-directory /volume1/docker/info-triage \
    logs --tail=100
```

Follow continuously:

``` bash
sudo docker compose \
    -f /volume1/docker/info-triage/compose.yaml \
    --project-directory /volume1/docker/info-triage \
    logs -f --tail=100
```

`Ctrl-C` stops following the logs; it does not stop the container.

The restricted passwordless sudo rule intentionally does **not** grant
arbitrary passwordless Docker/log access. Monitoring can therefore be
done in DSM or interactively with normal sudo authentication. If
passwordless CLI monitoring is later desired, create a separate narrowly
scoped root-owned command rather than granting unrestricted Docker
access.

------------------------------------------------------------------------

## 18. Verify the final security properties

### SSH key works

``` bash
ssh server 'whoami'
```

Expected:

``` text
deploy
```

without a password prompt.

### rsync works

``` bash
rsync -av /tmp/rsync-test.txt server:/volume1/docker/info-triage/
```

should succeed without a password.

### Restricted deployment works

``` bash
ssh server 'sudo -n /usr/local/sbin/deploy-container info-triage'
```

should succeed without a password.

### Arbitrary Docker is not passwordless

``` bash
ssh server 'sudo -n /usr/local/bin/docker ps'
```

should fail because a sudo password is required.

### Application is reachable

``` bash
curl http://192.168.1.10:8000
```

Expected:

``` text
Info Triage is running
```

### SSH is not deliberately exposed through the router

There should be no router port-forward mapping:

``` text
WAN:22 → 192.168.1.10:22
```

and no equivalent UPnP mapping.

------------------------------------------------------------------------

## 19. Important remaining security hardening

The current setup is convenient and limits *direct* passwordless Docker
execution, but there is one important caveat.

`deploy` can currently `rsync` these files into:

``` text
/volume1/docker/info-triage
```

including:

``` text
compose.yaml
Dockerfile
```

The root-owned `deploy-container` command then asks Docker to
execute/build from those user-controlled definitions.

Therefore the restricted command is **not yet a complete privilege
boundary**. Someone who gains control of the `deploy` account could
alter `compose.yaml` or `Dockerfile` to request dangerous Docker
behavior and then invoke the allowed root deployment command.

For stronger isolation, the production design should become:

``` text
Mac
 |
 | rsync as deploy
 v
/volume1/docker-staging/info-triage/
        |
        | restricted root deployment
        v
/volume1/docker/info-triage/
    ├── compose.yaml       root-controlled
    ├── Dockerfile         root-controlled
    └── src/               copied from staging
```

The root-owned Compose/Docker configuration would define exactly what
the application is permitted to run, while `deploy` would only be able
to update application source.

For a single-user home NAS this can be implemented as a second hardening
step after the basic workflow is confirmed, but it is important not to
mistake the current wrapper script for a fully secure Docker privilege
boundary.

------------------------------------------------------------------------

## 20. Final workflow

Day-to-day development is:

``` bash
# Edit in VS Code

./run.sh
```

Test locally at:

``` text
http://localhost:8000
```

Then commit/push with Git as desired.

When ready to deploy:

``` bash
./deploy.sh
```

The complete deployment path is:

``` text
VS Code / Mac
     |
     | local Docker test
     v
docker compose
     |
     | explicit ./deploy.sh
     v
rsync over SSH key
     |
     v
/volume1/docker/info-triage
     |
     | restricted sudo
     v
/usr/local/sbin/deploy-container info-triage
     |
     v
Docker Compose
     |
     v
info-triage
     |
     v
192.168.1.10:8000
```

This keeps development and testing on the Mac while making the Synology
an explicit, always-on deployment target.
