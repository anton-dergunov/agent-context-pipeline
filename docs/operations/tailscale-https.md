# Reaching the server from elsewhere, over HTTPS

The server speaks plain HTTP on port 8000, to the local network only. The dashboard is
unauthenticated and captures are protected by the bearer token alone, so the port should never be
forwarded on a router. To capture from outside the house, and to give the browser extension an HTTPS
address, put the server on a private network. This page uses [Tailscale](https://tailscale.com/);
any equivalent works.

Tailscale gives every machine a stable name such as `my-server.my-tailnet.ts.net` and can terminate
real HTTPS for that name, with certificates it provisions and renews itself. A trusted certificate
is not obtainable for a LAN address such as `192.168.1.10`, because no public authority issues one
for a private IP address, so HTTPS is only available under the tailnet name.

## Setup

1. Install Tailscale on the server and on every device you capture from, signed in to the same
   tailnet.
2. In the Tailscale admin console, enable **MagicDNS** and **HTTPS certificates**.
3. On the server, forward an HTTPS port to the daemon:

   ```bash
   sudo tailscale serve --bg --https=8443 http://127.0.0.1:8000
   tailscale serve status
   ```

`--bg` stores the mapping in Tailscale's own state, so this is a one-time setup that survives
restarts of the server and of the container; `deploy.sh` does not repeat it.

Port 8443 and not the default 443, so that this mapping does not take the place of another service
already served on the tailnet name. Any free port will do.

Use `tailscale serve`, never `tailscale funnel`: Funnel publishes a service to the whole internet.

## Using it

```text
https://my-server.my-tailnet.ts.net:8443/
```

reaches the same dashboard as `http://192.168.1.10:8000/`, from any device on the tailnet.

- **Browser extension**: enter that address as the Server URL on its options page.
- **Command line**: `INFO_TRIAGE_CAPTURE_URL=https://my-server.my-tailnet.ts.net:8443` in `.env`.
- **`deploy.sh` and `sync.sh`** keep using SSH, with whatever address the SSH alias has. Pointing
  the alias's `HostName` at the tailnet name makes them work from outside the house too.

`https://my-server.my-tailnet.ts.net:8000/` does not work and fails with a protocol error: port 8000
only ever speaks HTTP.

## Without passwordless root

`tailscale serve` needs root, because it writes to the Tailscale daemon's state. On a server where
the deployment account has no general `sudo`, such as the Synology setup in
[`synology.md`](synology.md#the-restricted-deployment-command), use the same pattern as the deploy
command: one root-owned script that does exactly this, and a sudoers rule for exactly that script.

```sh
#!/bin/sh
# /usr/local/sbin/enable-tailscale-serve, owned by root, mode 755
set -eu
exec /usr/local/bin/tailscale serve --bg --https=8443 http://127.0.0.1:8000
```

```sudoers
deploy ALL=(root) NOPASSWD: /usr/local/sbin/enable-tailscale-serve
```

This is narrower than `tailscale up --operator=deploy`, which would also let that account run
`tailscale funnel`.
