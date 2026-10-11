# Cloudflare phone connectivity

The full-app launcher, `python start.py`, manages **two** quick tunnels: one for
backend API/WebSockets and one for the phone PWA. It starts the backend and waits
for `/health` before starting either tunnel. The PWA tunnel targets the local
HTTP development server; it does not disable certificate validation to proxy a
self-signed HTTPS server.

The launcher sets `JANUS_TUNNEL_MODE=off` on the backend so it does not start a
second competing backend tunnel. It publishes ready origins through an atomic,
private JSON file selected by `JANUS_PUBLIC_URLS_FILE`:

```json
{"backend": "https://backend-name.trycloudflare.com", "pwa": "https://phone-name.trycloudflare.com"}
```

An unavailable service is represented by `null`. URLs contain only HTTPS origins;
credentials, paths, query parameters and fragments are rejected. The file is
read on each pairing request, so recovered tunnel URLs are advertised without
restarting the backend. No credentials are placed in these URLs.

`PUBLIC_BACKEND_URL` and `PWA_PUBLIC_URL` override their respective file values.
Use these variables for a stable deployed HTTPS backend or phone application.

`POST /api/v1/studio/pairing` returns the public backend `host`, optional `pwa_url`,
a single-use 60-second code and `status: "ready"`. If the launcher file is set
but the backend origin is unavailable, the endpoint returns HTTP 503 with
`PUBLIC_BACKEND_UNAVAILABLE` **before creating a pairing code**. This prevents
QR codes that point a phone at its own loopback address. Invalid local origin
configuration returns a generic HTTP 503 error without echoing its contents.

## Standalone backend

Standalone tunneling is opt-in. `JANUS_TUNNEL_MODE=auto` starts the
optional quick backend tunnel in a background task. If mode is unset, the
legacy `JANUS_AUTO_TUNNEL=1` flag also enables it; otherwise it remains off. Cloudflare connection
failures do not delay HTTP startup or block local API access. Set
`JANUS_BACKEND_PORT` to match the port used by uvicorn; its default is 8000.
Set `JANUS_TUNNEL_MODE=off` when a reverse proxy, named tunnel or full launcher
already manages public connectivity.

`cloudflared` must be installed and available on `PATH`. The Python `cloudflare`
package is an API client and does not install the tunnel executable. On macOS,
install it with `brew install cloudflared`; on Windows use
`winget install --id Cloudflare.cloudflared` or Cloudflare's official binary.

The standalone manager runs:

```text
cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:<JANUS_BACKEND_PORT>
```

An assigned `trycloudflare.com` hostname alone is not connection readiness. The
manager publishes it only after a `Registered tunnel connection` log message,
and clears it as soon as the process exits or its output closes. Concurrent
starts share one subprocess. Startup has a bounded timeout; cancellation,
connection failure and server shutdown terminate and reap the child. A stubborn
child receives a kill after the termination timeout.

Without a launcher URL file, Studio pairing retains the current HTTPS request
origin, a live automatic tunnel, or the local development request address (with
`status: "local"`). A loopback address is suitable only for local testing; a
phone needs a public HTTPS backend and a reachable PWA.

## Troubleshooting

- **Executable not found:** install the `cloudflared` CLI and reopen the shell.
- **Connection never registers:** inspect the tunnel log and check network or
  firewall access to Cloudflare. Local backend startup remains available.
- **Pairing returns `PUBLIC_BACKEND_UNAVAILABLE`:** the launcher has not yet
  published a live backend origin, or its tunnel is recovering. Retry when the
  launcher reports the public services as ready.
- **Link stopped working after a restart:** quick tunnel hostnames change. Make
  a new QR/manual pairing link after recovery, or configure stable public URLs.
- **Custom backend port:** use the same port in the launcher/uvicorn and
  `JANUS_BACKEND_PORT` for standalone auto mode.

Regression tests cover readiness before publication, simultaneous startup,
unexpected exit/recovery, missing executable, output failures, timeout,
cancellation, forceful cleanup, and dynamic pairing-origin replacement.
