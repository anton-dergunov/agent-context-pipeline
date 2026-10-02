# HTTPS Access to Info Triage via Tailscale

The dashboard at `http://192.168.1.10:8000` is plain HTTP, LAN-only, and
protected only by network reachability plus the `POST /capture` bearer token
(`GET /` is deliberately unauthenticated). Tailscale is a
second, separate way to reach the NAS, including from outside the LAN, and
Tailscale can also terminate real HTTPS for its own hostname
(`server.example-tailnet.ts.net`) using certificates it provisions and renews
automatically. Plain `https://server.example-tailnet.ts.net:8000/` fails with
`ERR_SSL_PROTOCOL_ERROR` because port 8000 only ever speaks HTTP; this sets up
a genuine HTTPS endpoint instead.

A real trusted HTTPS certificate is not obtainable for the LAN IP
(`192.168.1.10`) — no public CA issues certificates for private IP
addresses — so this only applies to the Tailscale hostname.

## Why a dedicated port

`tailscale serve status` already shows an existing mapping on the default
HTTPS port:

```text
https://server.example-tailnet.ts.net (tailnet only)
|-- / proxy http://127.0.0.1:8080
```

Port 8080 is FreshRSS, not info-triage — confirmed by `curl
http://127.0.0.1:8080/`, which returns FreshRSS's login redirect. That mapping
must stay untouched, so info-triage's HTTPS mapping uses a different port,
`8443`, rather than replacing it.

## One-time root setup on the NAS

`tailscale serve` requires root (it writes to `tailscaled`'s local state).
Following the same least-privilege pattern
[`synology-deployment.md`](synology-deployment.md#6-the-restricted-deployment-command) uses for
`deploy-container` — a narrowly scoped, single-purpose root wrapper plus a
matching sudoers rule — rather than granting `deploy` broad Tailscale control
(`tailscale up --operator=...` would also permit `tailscale funnel`, which
exposes a service to the public internet; this setup deliberately avoids
that).

As root on the NAS (`ssh server` then `sudo -i`):

```sh
cat > /usr/local/sbin/enable-tailscale-serve <<'EOF'
#!/bin/sh
set -eu
exec /usr/local/bin/tailscale serve --bg --https=8443 http://127.0.0.1:8000
EOF
chown root:root /usr/local/sbin/enable-tailscale-serve
chmod 755 /usr/local/sbin/enable-tailscale-serve

cat > /etc/sudoers.d/tailscale-serve <<'EOF'
deploy ALL=(root) NOPASSWD: /usr/local/sbin/enable-tailscale-serve
EOF
chown root:root /etc/sudoers.d/tailscale-serve
chmod 440 /etc/sudoers.d/tailscale-serve
visudo -c

/usr/local/sbin/enable-tailscale-serve
```

`tailscale serve --bg` persists the mapping in `tailscaled`'s state, so this
is a one-time setup, not something `deploy.sh` needs to repeat on every
deployment. It survives NAS/container restarts as long as the Tailscale
package itself keeps running.

## Verifying and reapplying from the Mac over SSH

The sudoers rule covers exactly the wrapper script, so this works
non-interactively, the same way `deploy.sh` calls `deploy-container`:

```bash
ssh server 'sudo -n /usr/local/sbin/enable-tailscale-serve'
ssh server '/usr/local/bin/tailscale serve status'
```

`tailscale status` and `tailscale serve status` are both read-only and never
need sudo. By contrast, `ssh server 'sudo -n /usr/local/bin/tailscale serve ...'`
with any other target should fail with `sudo: a password is required` —
sudoers matches the literal command line, so only the exact wrapper-script
invocation is passwordless.

## Access

```text
https://server.example-tailnet.ts.net:8443/
```

reaches the same dashboard as `http://192.168.1.10:8000/`, over a
Tailscale-issued certificate, from any device on the tailnet — Tailscale
handles renewal, so there is nothing to maintain here beyond the one-time
setup above.
