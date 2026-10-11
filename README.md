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
    puts the backend on the public internet (behind its credentials) for as long as Janus runs. Without
    `cloudflared`, a pinned, SHA-256-verified copy is downloaded to `.janus/bin`.
  - `--pwa` also runs the PWA dev server on port 5174. Off by default: phones use the hosted PWA.
  - `--studio=browser|none` runs Studio in a browser tab, or not at all. `--no-agent` skips the agent.
  - `--only=agent` starts just the agent against a running backend (Studio’s **Start agent** button uses this).
- Janus only stops processes it started itself (tracked in `.janus/pids.json`). If something else holds port 8000,
  5173 or 5174, `npm start` names the process and exits.
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

## Link a phone

Studio runs at `http://127.0.0.1:5173`. Phones normally use the hosted PWA; with
`npm start -- --pwa` a dev server runs at `https://<laptop-LAN-IP>:5174` instead.
Open the PWA on your phone first. Camera scanning requires an HTTPS certificate
trusted by the phone; manual code entry does not need camera permission.

Start Janus with `npm start -- --tunnel` so the phone can reach the backend.
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
