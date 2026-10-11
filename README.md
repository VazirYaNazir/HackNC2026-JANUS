# JANUS

JANUS connects the desktop panel builder (`frontend/`), phone PWA (`PWA/`),
FastAPI backend, SQLite database, and a locally authorized laptop agent.
Both frontends share the same authenticated panel and live job client.

## Quick start

Install Node 20.19+ and [`uv`](https://docs.astral.sh/uv/) (it fetches Python 3.12+), then run from the repository root:

```sh
npm start
```

The launcher installs locked dependencies, preserves existing registrations and environment settings, starts the backend, SQLite, Electron Studio and phone PWA, and creates HTTPS Cloudflare connections for phone pairing. Studio connects automatically through a single-use local credential handoff. On macOS or Windows, the terminal asks whether JANUS may control this laptop for the session. Type `yes` to enable the native tools and screen mirror; declining keeps the web services running. macOS also requires Accessibility and Screen Recording permission, and Windows native compilation requires the Visual Studio C tools and Windows SDK.

Choose **Project → Laptop controls starter**, then **Save & Push → Pair Device**. Open the HTTPS phone app address printed in the terminal and scan the QR, paste the pairing link, or enter its backend address and code. A connected tunnel can take longer to become publicly reachable: the launcher keeps it running, retries its HTTPS check, and publishes each ready address independently. While the backend address is pending, pairing reports `PUBLIC_BACKEND_UNAVAILABLE`; click **New code** once the terminal prints **Phone backend address ready**. A tunnel restart changes its address: generate a new pairing code.

Public checks report HTTP status, DNS, TLS certificate, or network failures instead of shutting down healthy tunnels. HTTPS checks and the cloudflared installer preserve certificate and hostname verification, adding the locked backend's public CA bundle to Python's system trust. Repeated connector failures are retried up to three times; local Studio stays available. Inspect `.janus/logs/tunnel-backend.log` and `.janus/logs/tunnel-pwa.log` for connector errors.

```sh
npm start -- --local-only                    # local development without public tunnels
npm start -- --local-only --no-agent         # desktop development without laptop permissions
npm start -- --studio=browser                # optional browser Studio instead of Electron
npm start -- --studio=none --no-agent        # backend and phone app without Studio
npm start -- --no-screen                     # approve controls without screen capture
npm start -- --only=agent                    # approve/start an agent against an existing backend
npm run start:imessage                       # also start the configured messaging sidecar
npm run start:demo                           # compatibility shortcut; includes messaging
npm run doctor                              # diagnose optional integrations and prerequisites
```

`--no-install` uses installed dependencies; `--no-browser` keeps the local servers running without opening Electron or a browser tab. `--device-id`, `--backend-port`, `--admin-port`, `--studio-port` and `--pwa-port` select the laptop and local ports. Legacy `--pwa`, `--tunnel` and `--screen` flags remain accepted because those features are now included by default. The wrapper also accepts flags npm places in `npm_config_<flag>` on Windows.

### Startup permissions and private state

Startup creates `agent/.validation-policy.json` with all supported capabilities and the maximum supported validation budgets if no policy exists. It preserves custom scopes and explicit job grants. The file alone grants no approval: interactive terminal consent or Electron's local permission dialog activates the session, and approval ends when the agent exits. An unattended launch never approves itself. Generated jobs remain scoped, bounded, replay protected, and require an audited isolation adapter; see **AI widgets** below.

The launcher uses private local files in `.janus/`: registrations, the manual controller credential, SQLite, process records, tunnel origins and redacted service logs. POSIX permissions and Windows owner ACLs protect sensitive launcher files. Existing `backend/.env` and its credentials are preserved. On first startup the previous `backend/app/app.db` is copied using SQLite backup, including committed WAL data, into writable `.janus/janus.sqlite`; the original is preserved. Set `JANUS_DB_PATH` to use a different writable database. `JANUS_STATE_DIR` can select another private state directory.

Ctrl+C stops the owned process trees and clears published phone origins. Verified PID records prevent a second launcher from replacing an active session and let the next startup clean up its own leftovers after a crash. A stopped agent or messaging sidecar leaves the web services running. Studio receives its credential once through an origin-restricted loopback handoff, keeps it only in memory, and removes the handoff fragment immediately. Electron is the default interface and also supports local IPC and a permission dialog for **Start agent**. Use `--studio=browser` for browser development; the default launcher installs the Electron runtime when needed.

`npm run setup` remains available for manual prerequisite checks, environment initialization, native builds and optional Tiger Data schema setup. It preserves configured values. Standalone backend startup uses `JANUS_AUTO_TUNNEL=1` or `JANUS_TUNNEL_MODE=auto` for its own tunnel; the full launcher owns its tunnels and disables duplicate backend tunneling.

See the [widget integration audit](frontend/docs/widget-integration.md) for every widget family, live telemetry sources, generated/custom actions and hardware requirements.

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

Running a generated program needs a local grant: either a fresh job grant minted within the session you approved at startup, or an explicit request-ID entry in the agent’s local policy file. Confirming in Studio or the phone never grants OS permissions. Startup approval does not supply the required isolation adapter. See the [validation protocol](agent/docs/validation-protocol.md#local-policy).

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

Studio runs at `http://127.0.0.1:5173`. `npm start` includes a PWA server on loopback port 5174 and publishes it through a separate trusted HTTPS Cloudflare address. Open the printed **Phone app** address on your phone. Camera scanning needs HTTPS and camera permission; manual entry does not need camera permission. A configured hosted `PWA_PUBLIC_URL` takes precedence.

Start JANUS with `npm start` so the phone can reach the backend.
In Studio, select **Pair Device**. The dialog uses the backend's Cloudflare
tunnel when one is running and Studio is connected locally, or its existing HTTPS
backend address. Set `PUBLIC_BACKEND_URL` in `backend/.env` for a stable/custom
HTTPS backend origin, or edit **Phone backend address** in the dialog to the
URL printed by `cloudflared tunnel --url http://127.0.0.1:8000`.

On the phone choose **Scan to add panel**, or **Enter code instead** to paste the full pairing link or enter the HTTPS backend address and code shown in Studio’s Pair Device dialog. Codes expire after 60 seconds and can be used once;
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

Native processes start concurrently, with separate readers and bounded queues; commands for a given tool remain ordered. These built-in operations use the laptop's explicit local startup permission. Generated programs still need a fresh scoped job grant, obtained from your approved startup session or explicit local policy, and an audited isolation adapter. Gemini suggestions remain opt-in for each generated execution.

Project names, icons, colors, toggle styles, ranges, steps, source bindings, and thresholds can be edited in Studio. Project import/export stores layout JSON without credentials or generated source. The supplied JANUS logo is used in both apps, browser icons, PWA installation icons and Electron.

Run `npm test` from the repository root to check the complete application. See [detailed test commands and hardware verification](docs/testing.md).
