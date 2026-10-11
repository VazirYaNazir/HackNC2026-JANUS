# @janus/pwa

The phone panel app: React + Vite + TypeScript. Part of the npm workspace at the repo root.

## Develop

```
npm ci                 # from the repo root, never in this folder
npm run dev --workspace @janus/pwa -- --host 0.0.0.0 --port 5174 --strictPort
```

Open `https://<laptop-LAN-IP>:5174` on a phone on the same Wi-Fi. Camera scanning
requires the development certificate to be trusted by the phone, or a deployed
PWA on a trusted HTTPS host. If camera access is unavailable, use manual entry.

Typecheck: `npx tsc -b`. Lint: `npm run lint`. Tests: `npm test` (vitest; pure logic only, in `tests/`). Production build: `npm run build` (output in `dist/`).

## Deploy to Vercel

1. Import the repo and set **Root Directory** to `PWA`.
2. Leave **Include source files outside of the Root Directory** enabled. The app imports `shared/*` (`@janus/design`, `@janus/schema`, `@janus/widgets`), which live outside `PWA/`.
3. Everything else comes from `vercel.json`: install from the repo root (`npm ci`), build with `npm run build`, serve `dist/`, and rewrite unknown paths to `index.html`.
4. Use Node 20.19 or newer (Project Settings, Node.js Version).

Pairing uses the backend address and one-time code from Studio's QR. The phone
exchanges that code through `/api/pair` for a credential scoped to one laptop;
no credentials are bundled into the build. Both the current JSON QR and pairing
URL formats are supported, including the complete 12-character scoped code.

From the home screen, **Scan to add panel** opens the camera. **Enter code
instead** opens separate **PC address** and **Pairing code** fields without
requesting camera access. **Paste pairing link** accepts the full link shown
in Studio. Invalid/expired/used codes, network failures, and camera failures
have separate messages. Failed manual attempts retain the form and its values.
The credential is saved immediately after pairing; **Save** names the panel.

Set the optional `PWA_PUBLIC_URL` backend variable to this app's deployed HTTPS
origin to let Studio's QR open the PWA directly from a phone's Camera app.
Opening that link fills the manual form and removes the code from the browser
address; no exchange happens until **Pair** is pressed. The backend address in
the link is separate from the PWA's hosting address.

Opening a saved panel fetches its published layout, connects to authenticated
live updates, and restores recent requests from SQLite. Controls show the
request and any similar program suggestions before submission; reuse requires
explicit selection. Locally shared JSON results update display widgets, and
recent requests show progress, outcomes, and cancellation. The home screen's
online indicator checks the authenticated laptop session.

Remote backends must use HTTPS/WSS. Studio's pairing dialog obtains the public
address from the backend's automatic tunnel, its existing HTTPS connection,
or the optional `PUBLIC_BACKEND_URL`. Its editable **Phone backend address**
must be reachable from the phone: a phone cannot reach another computer's
`127.0.0.1`. Codes last 60 seconds and are single-use. If the backend tunnel
changes, choose **Options → Pair again** on the saved panel; its name and ID
are preserved, and linking it to a different laptop is rejected. See the
[connected-panel guide](../backend/app/docs/connected-panels.md) for grants,
database persistence, and current telemetry limits.

## Install

The app is installable. `vite-plugin-pwa` (see `vite.config.ts`) generates the web manifest and a service worker that precaches the app shell; the theme and background colors come from `--surface` in `shared/design/tokens.css`. Icons live in `public/`.

The home screen shows an install banner: a native "Install JANUS" button on Android/Chrome, and "Tap Share, then Add to Home Screen" on iOS. It is hidden when the app is already running installed. On iOS the installed app has its own storage, so install before adding panels.
