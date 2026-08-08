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
-   NAS deployment directory: `/volume1/docker/hello`
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
/volume1/docker/hello
```

Create it if necessary:

``` bash
mkdir -p /volume1/docker/hello
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
echo hello >/tmp/rsync-test.txt

rsync -av \
    /tmp/rsync-test.txt \
    server:/volume1/docker/hello/
```

Successful output should resemble:

``` text
sending incremental file list
rsync-test.txt
```

Verify:

``` bash
ssh server 'cat /volume1/docker/hello/rsync-test.txt'
```

Expected:

``` text
hello
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
    hello)
        PROJECT_DIR="/volume1/docker/hello"
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
/usr/local/sbin/deploy-container hello
```

The script deliberately accepts only the named `hello` deployment.

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
deploy ALL=(root) NOPASSWD: /usr/local/sbin/deploy-container hello
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
deploy ALL=(root) NOPASSWD: /usr/local/sbin/deploy-container hello
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
ssh server 'sudo -n /usr/local/sbin/deploy-container hello'
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
└── run.sh
```

The Mac is used for:

-   VS Code development
-   local Docker testing
-   Git commits
-   GitHub
-   explicit deployment

The NAS copy is a deployment target.

------------------------------------------------------------------------

## 11. Hello-world application

`app.py`:

``` python
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Hello from my Synology NAS!\n")


server = HTTPServer(("0.0.0.0", 8000), Handler)

print("Server listening on port 8000")
server.serve_forever()
```

The server deliberately binds to:

``` text
0.0.0.0:8000
```

rather than `127.0.0.1`, so Docker can expose it outside the container.

------------------------------------------------------------------------

## 12. Dockerfile

`Dockerfile`:

``` dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY app.py .

CMD ["python", "app.py"]
```

`curl` is installed inside the image because the Compose health check
uses it.

------------------------------------------------------------------------

## 13. Docker Compose configuration

`compose.yaml`:

``` yaml
services:
  hello:
    build: .
    container_name: hello-synology
    restart: unless-stopped

    ports:
      - "8000:8000"

    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 5s
```

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
curl http://localhost:8000
```

Expected:

``` text
Hello from my Synology NAS!
```

Although the message says "Synology NAS", during this test the same
application is actually running locally in Docker on the Mac.

Stop the foreground Compose process with `Ctrl-C`.

This local test is intentionally separate from deployment.

------------------------------------------------------------------------

## 15. One-command deployment from the Mac

`deploy.sh`:

``` bash
#!/usr/bin/env bash

set -euo pipefail

REMOTE="server"
REMOTE_DIR="/volume1/docker/hello"
URL="http://192.168.1.10:8000"

echo "==> Copying files to NAS"

rsync -az --delete \
    --exclude '.git/' \
    --exclude '.env' \
    --exclude 'data/' \
    --exclude 'logs/' \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    ./ "${REMOTE}:${REMOTE_DIR}/"

echo "==> Building and restarting container"

ssh "$REMOTE" \
    'sudo -n /usr/local/sbin/deploy-container hello'

echo "==> Waiting for service"

sleep 3

echo "==> Health check"

curl --fail --silent --show-error "$URL"

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
3.  Passwordless execution of only `deploy-container hello`.
4.  `docker compose up -d --build` as root.
5.  Display of Compose container status.
6.  A three-second wait.
7.  An HTTP request from the Mac to verify that the deployed service is
    reachable.

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
__pycache__/
.DS_Store
```

For a real Telegram bot, `.env` will typically contain deployment
secrets such as the Telegram bot token and must not be committed to Git.

Persistent runtime data should similarly live in an excluded directory
such as:

``` text
data/
```

------------------------------------------------------------------------

## 17. Monitoring

### DSM GUI

Open:

**DSM → Container Manager → Container**

The `hello-synology` container can be inspected there for:

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
    -f /volume1/docker/hello/compose.yaml \
    --project-directory /volume1/docker/hello \
    ps
```

### Logs

On the NAS:

``` bash
sudo docker compose \
    -f /volume1/docker/hello/compose.yaml \
    --project-directory /volume1/docker/hello \
    logs --tail=100
```

Follow continuously:

``` bash
sudo docker compose \
    -f /volume1/docker/hello/compose.yaml \
    --project-directory /volume1/docker/hello \
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
rsync -av /tmp/rsync-test.txt server:/volume1/docker/hello/
```

should succeed without a password.

### Restricted deployment works

``` bash
ssh server 'sudo -n /usr/local/sbin/deploy-container hello'
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
Hello from my Synology NAS!
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
/volume1/docker/hello
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
/volume1/docker-staging/hello/
        |
        | restricted root deployment
        v
/volume1/docker/hello/
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
/volume1/docker/hello
     |
     | restricted sudo
     v
/usr/local/sbin/deploy-container hello
     |
     v
Docker Compose
     |
     v
hello-synology
     |
     v
192.168.1.10:8000
```

This keeps development and testing on the Mac while making the Synology
an explicit, always-on deployment target.
