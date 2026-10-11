# Janus

Janus connects the desktop panel builder (`frontend/`), phone PWA (`PWA/`),
FastAPI backend, SQLite database, and a locally authorized laptop agent.
Both frontends share the same authenticated panel and live job client.

## Development

Use Node 20.19+ and Python 3.12+ with `uv`. Install JavaScript workspaces from
the repository root:

```sh
npm ci
cd backend
uv sync --locked
```

Create `backend/.env` from `.env.example` only if it does not already exist.
Configure laptop registrations and a Gemini credential as described in
[backend–agent setup](backend/app/docs/backend-agent.md). Then start the
backend from `backend/`:

```sh
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env --reload
```

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

Studio runs at `http://127.0.0.1:5173`; the PWA dev server runs at
`https://<laptop-LAN-IP>:5174`. Open the PWA on your phone first. Camera scanning
requires an HTTPS certificate trusted by the phone; manual code entry does not
need camera permission. Alternatively deploy the PWA to a trusted HTTPS host.

In Studio, select **Pair Device**. The dialog uses the backend's automatic
Cloudflare tunnel when Studio is connected locally, or its existing HTTPS
backend address. Set `PUBLIC_BACKEND_URL` in `backend/.env` for a stable/custom
HTTPS backend origin, or edit **Phone backend address** in the dialog to the
URL printed by `cloudflared tunnel --url http://127.0.0.1:8000`.

On the phone choose **Scan to add panel**, or **Enter code instead** and copy
the displayed backend address and complete 12-character code. **Paste pairing
link** is also available. Codes expire after 60 seconds and can be used once;
choose **New code** in Studio to retry. The connection is saved as soon as
pairing succeeds, before naming the panel.

Optionally set `PWA_PUBLIC_URL=https://your-phone-app.example` in `backend/.env`
and restart the backend. Studio's QR then opens that PWA using the phone's
Camera app, with the address/code filled in; tap **Pair** to approve linking.
These settings contain HTTPS origins, without paths or credentials. PWA and
backend origins are different services. If a tunnel address changes, use the
saved panel's **Options → Pair again** to update its connection.
