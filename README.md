# JANUS

JANUS connects the desktop panel builder (`frontend/`), phone PWA (`PWA/`),
FastAPI backend, SQLite database, and a locally authorized laptop agent.
Both frontends share the same authenticated panel and live job client.

## Quick start

```sh
npm run setup   # checks prerequisites, installs dependencies, creates backend/.env, builds native tools
npm start       # backend + laptop agent + Studio
```

Prerequisites: Node 20.19+ and [`uv`](https://docs.astral.sh/uv/) (it fetches Python 3.12+). In Studio, choose
**Connect backend → Use this laptop’s local backend**; nothing needs pasting. To pair a phone, add `-- --tunnel`
(`npm start -- --tunnel`) and use **Pair Device**.

**On Windows, use the shortcut scripts.** PowerShell drops the `--` that separates npm's options from the script's,
so `npm start -- --imessage` reaches npm as its own (unknown) option. The scripts below have their flags built in:

```sh
npm run start:demo       # backend + agent + Studio + iMessage + screen sharing + tunnel  (--imessage --screen --tunnel)
npm run start:imessage   # the same without screen sharing or the tunnel                (--imessage)
```

The scripts also read flags that npm swallowed (`npm_config_<flag>`), so `npm start --imessage` works too, with an
"Unknown env config" warning from npm that you can ignore.

### What the scripts do

- `npm run setup` runs the preflight checks first and stops on blocking problems before installing anything. It
  is safe to re-run: it adds any keys that `backend/.env.example` gained since your `.env` was written, never
  changes values you already set, generates `ADMIN_SECRET` and this laptop’s two credentials only when empty,
  and restricts `backend/.env` to your user (`chmod 600` / an owner-only Windows ACL). Options:
  `--skip-native`, `--reinstall`, `--pwa-url=https://…` (or `JANUS_PWA_URL`, or `scripts/defaults.json`) to set
  `PWA_PUBLIC_URL` so the pairing QR opens the hosted PWA. If none is configured that value stays empty.
- `npm start` starts the backend, waits for `/health`, then the agent (`--native-tools`) and Studio. Closing
  Studio stops everything. Flags (pass after `--`):
  - `--tunnel` exposes the backend through a Cloudflare tunnel for phone pairing. **Off by default**, because it
    puts the backend on the public internet (behind its credentials) for as long as JANUS runs. Without
    `cloudflared`, a pinned, SHA-256-verified copy is downloaded to `.janus/bin`.
  - `--pwa` also runs the PWA dev server on port 5174. Off by default: phones use the hosted PWA.
  - `--studio=browser|none` runs Studio in a browser tab, or not at all. `--no-agent` skips the agent.
  - `--only=agent` starts just the agent against a running backend (Studio’s **Start agent** button uses this).
- JANUS only stops processes it started itself, recorded in `.janus/pids.json` with each process's start time (so a
  recycled PID is never mistaken for one of ours). Ctrl+C, a closed console, or a crash kills every child's whole
  process tree (`taskkill /T /F` on Windows): backend, agent, iMessage sidecar, Vite and Electron. If the
  orchestrator itself is hard-killed and leaves children behind, the next `npm start` stops exactly those recorded
  leftovers. A second `npm start` while one is healthy refuses ("JANUS is already running") instead of killing it,
  and if something else holds port 8000, 5173 or 5174 it names the process and exits.
- One agent per laptop. Studio's **Start agent** button only appears while no agent is connected, never while one
  is running or starting, and `npm start -- --only=agent` refuses when a recorded agent is alive. The backend also
  refuses a second connection for the same device (close code 4409), and an agent that receives that stops instead
  of retrying forever.
- `npm run doctor` runs the same checks on demand. Setup and start run them automatically and print failures only.

### Studio and the laptop agent

In the Electron Studio, the credential for this laptop is read by Electron’s main process from `backend/.env`
and handed to the window over IPC; only the dev-server page can request it, and there is no HTTP endpoint that
returns it. In a plain browser Studio, paste the controller credential instead. When no agent is connected,
Electron shows a **Start agent** button; a browser shows the command to copy.

## Manual setup

The scripts above wrap these steps. Use them directly if you prefer.

```sh
npm ci
cd backend && uv sync --locked
```

Create `backend/.env` from `.env.example` only if it does not already exist.
Configure laptop registrations and a Gemini credential as described in
[backend–agent setup](backend/app/docs/backend-agent.md). Then start the
backend from `backend/`:

```sh
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env --reload
```

The backend starts its Cloudflare tunnel only when `JANUS_AUTO_TUNNEL=1` is set in its environment.
In separate terminals, from the repository root:

```sh
npm run dev --workspace frontend
npm run dev --workspace @janus/pwa -- --host 0.0.0.0 --port 5174 --strictPort
```

In Studio, choose **Connect backend**, enter the backend address and the
laptop's controller credential, add a control, and configure its action with
the assistant. **Save & Push** publishes the panel. **Pair Device** creates a
single-use QR/code for the PWA. Both applications show live request progress
and results; similar validated programs are offered for explicit reuse.

The laptop must run `agent.main connect` with its own credential, local grants,
and an installed isolation adapter before generated programs can execute.
Remote connections require HTTPS/WSS. See the complete
[connected-panel workflow and verification commands](backend/app/docs/connected-panels.md)
for pairing, persistence, permissions, deployment, and current limits.

## AI widgets (optional)

Built-in controls (volume, mouse, metrics, processes, screen) need none of this. AI-generated widgets need two
things, and Studio’s status line above the assistant says which one is missing before you generate anything.
`npm run doctor` reports both as warnings.

### Gemini key

The backend generates programs with Gemini. `npm run setup` asks for a key (input is hidden; press Enter to skip) and
writes it to `backend/.env`. In scripts, set `JANUS_GEMINI_KEY` instead of passing it on the command line. You can
also set `GEMINI_API_KEY` in `backend/.env` yourself and restart the backend.

### Isolation adapter

Generated C is compiled and run only through a locally installed, audited isolation adapter, selected with
`AGENT_ISOLATION_ADAPTER=my_local_app.worker:create_boundary` in the agent’s environment (or `--isolation-adapter`).
No adapter ships with JANUS yet, so without one the laptop reports `BUILD_BOUNDARY_UNAVAILABLE` and never compiles
generated source. Neither the backend nor a policy file can enable an adapter. Details:
[local agent initialization](backend/app/docs/backend-agent.md#local-agent-initialization).

### Local authorization

Running a generated program also needs a grant that only the laptop can create: an entry for that request ID in the
agent’s local policy file (`--validation-policy`, read when the agent starts). Confirming in Studio or the phone
never grants OS permissions. See the [validation protocol](agent/docs/validation-protocol.md#local-policy).

## Text your laptop (iMessage)

Text JANUS from an allowlisted number and it answers from your laptop: *"status"*,
*"volume 30"*, *"lock"*, *"help"*. It uses [Photon Spectrum](https://photon.codes/docs/spectrum-ts/getting-started)
(cloud iMessage), so it works from any OS. It is off unless you ask for it:

1. Create a Photon Spectrum project and put its `SPECTRUM_PROJECT_ID` / `SPECTRUM_PROJECT_SECRET` in `backend/.env`.
2. Set `IMESSAGE_ALLOWED_SENDERS` there to your number(s) in E.164 form, comma-separated (`+15551234567`).
   Texts from anyone else are ignored without a reply. `npm run setup` generates `MESSAGING_SERVICE_TOKEN`.
3. `npm run start:imessage` (same as `npm start -- --imessage`). Use `npm run start:demo` to add the tunnel.
   `npm run doctor -- --imessage` checks the setup.

On startup the sidecar texts each allowlisted number "JANUS is online. Text 'help' for commands." at most once per
UTC day (the date is remembered, hashed, in `.janus/imessage-greeted.json`), because every first message to a new
recipient counts against the line's 50-new-conversations-per-day quota. Set `IMESSAGE_GREETING=0` to turn it off.

| Text | What happens |
| --- | --- |
| `status` (also `cpu`, `stats`) | CPU and RAM on one line, plus the temperature when the machine reports one. Anything unavailable is left out |
| `apps` (also `processes`) | The apps using the most CPU |
| `history` (also `slow`, `spikes`, "why was my laptop slow?") | The worst CPU minute of the last hour and the busiest apps in it, e.g. "CPU peaked at 94% at 4:31 PM. Busiest: Chrome, Code, Teams." Needs [Tiger Data](#tiger-data-optional-history) |
| `volume`, `volume 40` | Read / set the volume (0-100) |
| `mute`, `unmute` | Mute sets the volume to 0 right away; unmute restores the previous level |
| `lock` | Locks the PC. JANUS asks first: reply **YES** within 60 s |
| `help` | Lists the commands |

Anything else gets "Not sure what that means. Text help to see what I can do."

How it stays safe: the `imessage/` sidecar only forwards `{ sender, text, messageId }` to the backend over loopback with
a service token and sends back whatever the backend replies. The backend (`backend/app/messaging`) enforces the
allowlist, maps text to a fixed list of registered native actions (never code generation), asks for confirmation
before `lock`, drops redelivered message IDs, and answers "Your laptop is offline." when no agent is
connected. If a Gemini key is set, unrecognised text may be classified by Gemini, but only into that same action list
with validated arguments (set `IMESSAGE_AI_FALLBACK=0` to turn it off). The endpoint refuses anything that is not a
direct loopback request, including requests relayed through a tunnel. Studio shows "iMessage: connected / off".

The sidecar is built so one bad message cannot wedge it: each text is handled in its own task behind a per-sender
queue (the message stream is never awaited on a send), every send has a 15 s deadline, and message IDs from
the last 24 hours are saved (hashed) in `.janus/imessage-processed.json` and claimed *before* an action runs, so a
redelivery after a restart is skipped instead of repeated. Every inbound message is logged first (masked number and
type), then each stage (handling, backend reply, sending, sent or failed), never the text.

## Tiger Data (optional history)

An optional time-series store on [Tiger Cloud](https://www.tigerdata.com/) (TimescaleDB) that powers the last-hour
sparklines, the Recent activity panel and the iMessage `history` command. **SQLite stays the app database**, and
everything below is a no-op while `TIGER_DATABASE_URL` is empty.

1. Create a Tiger Cloud service and put its connection string in `backend/.env` as `TIGER_DATABASE_URL`
   (`postgres://tsdbadmin:…@HOST:PORT/tsdb?sslmode=require`).
2. `npm run setup` creates the schema (idempotent; it can also be run alone with `uv run --locked python -m app.tiger.migrate`
   in `backend/`). `npm run doctor` checks the connection and that the schema exists.

What gets stored: `metrics` (CPU %, RAM %, RAM used, temperature, one sample per metric every 5 s),
`process_samples` (the 10 busiest processes every 10 s) and `action_events` (every widget action, iMessage command and
generated-program dispatch, with its source, outcome and request ID; joystick moves are not logged). The schema adds
continuous aggregates `metrics_1m` and `metrics_1h` (avg/max/min) with refresh policies, compression after 1 day, and
30-day retention on the raw tables.

The backend never waits on Tiger: samples go through a bounded queue and are written in batches (every 5 s or 500 rows,
on a worker thread), IPv4 is preferred when connecting, and if the database is unreachable the samples are dropped with
one log line a minute. `GET /api/metrics/history?metric=cpu.percent&range=1h` reads `metrics_1m` and `GET /api/activity`
returns the last 50 actions; both answer `available: false` when Tiger is off or down, which simply hides the sparkline
and the panel in Studio and the phone app.

The backend test suite never touches your database. One opt-in integration test (`tests/test_tiger.py`) applies the schema,
writes rows under a throwaway `itest-…` device ID, reads them back through the aggregates and cleans up after itself. It runs
only when you export **both** `TIGER_DATABASE_URL` and `JANUS_TIGER_INTEGRATION=1` in your shell; having the URL in
`backend/.env` is deliberately not enough.

## Link a phone

Studio runs at `http://127.0.0.1:5173`. Phones normally use the hosted PWA; with
`npm start -- --pwa` a dev server runs at `https://<laptop-LAN-IP>:5174` instead.
Open the PWA on your phone first. Camera scanning requires an HTTPS certificate
trusted by the phone; manual code entry does not need camera permission.

Start JANUS with `npm start -- --tunnel` so the phone can reach the backend.
In Studio, select **Pair Device**. The dialog uses the backend's Cloudflare
tunnel when one is running and Studio is connected locally, or its existing HTTPS
backend address. Set `PUBLIC_BACKEND_URL` in `backend/.env` for a stable/custom
HTTPS backend origin, or edit **Phone backend address** in the dialog to the
URL printed by `cloudflared tunnel --url http://127.0.0.1:8000`.

On the phone choose **Scan to add panel**, or **Enter code instead** and paste
the full pairing link shown in Studio's Pair Device dialog. Codes expire after 60 seconds and can be used once;
choose **New code** in Studio to retry. The connection is saved as soon as
pairing succeeds, before naming the panel.

Optionally set `PWA_PUBLIC_URL=https://your-phone-app.example` in `backend/.env`
and restart the backend. Studio's QR then opens that PWA using the phone's
Camera app, with the address/code filled in; tap **Pair** to approve linking.
These settings contain HTTPS origins, without paths or credentials. PWA and
backend origins are different services. If a tunnel address changes, use the
saved panel's **Options → Pair again** to update its connection.

## Built-in laptop controls

The registered connection can now run the five repository-owned native tools alongside generated jobs. On macOS or Windows, set the laptop's own `AGENT_TOKEN` and start:

```sh
uv run --locked --directory agent python -m agent.main connect --url ws://127.0.0.1:8000/ws/agents/my-laptop --device-id my-laptop --native-tools --screen
```

Use the device ID from `AGENT_DEVICES_JSON`. Omit `--screen` to keep screen sharing off. Remote agents use `wss://`. macOS mouse control needs Accessibility permission and screen capture needs Screen Recording permission. CPU temperature availability depends on the hardware; `--temperature-file` accepts a trusted local sensor provider.

In Studio choose **Project → Laptop controls starter**, then **Save & Push** and pair the phone. The volume slider, mouse joystick and click buttons send fixed native commands; RAM, CPU temperature, relevant process trees and screen frames arrive live in both apps. CPU utilization is also available as `cpu.percent`. Process relevance defaults to at least 1% CPU or 100 MiB RAM, including parent context; adjust `--min-cpu` and `--min-ram-mb` when starting the agent. Joystick movement stops on release, lost focus, and the native mouse timeout.

Native processes start concurrently, with separate readers and bounded queues; commands for a given tool remain ordered. These built-in operations use the laptop's explicit local startup permission. Generated programs still need a fresh local job grant and an audited isolation adapter. Gemini suggestions remain opt-in for each generated execution.

Project names, icons, colors, toggle styles, ranges, steps, source bindings, and thresholds can be edited in Studio. Project import/export stores layout JSON without credentials or generated source. The supplied JANUS logo is used in both apps, browser icons, PWA installation icons and Electron.

Run `npm test` from the repository root to check the complete application. See [detailed test commands and hardware verification](docs/testing.md).
