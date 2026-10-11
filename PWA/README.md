# @janus/pwa

The phone panel app: React + Vite + TypeScript. Part of the npm workspace at the repo root.

## Develop

```
npm ci                 # from the repo root, never in this folder
cd PWA && npm run dev -- --host
```

The dev server runs over https (self-signed) so the camera works on a phone. Accept the certificate warning once.

Typecheck: `npx tsc -b`. Lint: `npm run lint`. Tests: `npm test` (vitest; pure logic only, in `tests/`). Production build: `npm run build` (output in `dist/`).

## Deploy to Vercel

1. Import the repo and set **Root Directory** to `PWA`.
2. Leave **Include source files outside of the Root Directory** enabled. The app imports `shared/*` (`@janus/design`, `@janus/schema`, `@janus/widgets`), which live outside `PWA/`.
3. Everything else comes from `vercel.json`: install from the repo root (`npm ci`), build with `npm run build`, serve `dist/`, and rewrite unknown paths to `index.html`.
4. Use Node 20.19 or newer (Project Settings, Node.js Version).

Pairing uses the backend address and one-time code from Studio's QR. The phone
exchanges that code through `/api/pair` for a credential scoped to one laptop;
no credentials are bundled into the build. Manual address/code entry works too.

Opening a saved panel fetches its published layout, connects to authenticated
live updates, and restores recent requests from SQLite. Controls show the
request and any similar program suggestions before submission; reuse requires
explicit selection. Locally shared JSON results update display widgets, and
recent requests show progress, outcomes, and cancellation. The home screen's
online indicator checks the authenticated laptop session.

Remote backends must use HTTPS/WSS. Use Studio through the same remotely
reachable address before creating a phone pairing code: a phone cannot reach
another computer's `127.0.0.1`. See the
[connected-panel guide](../backend/app/docs/connected-panels.md) for grants,
database persistence, and current telemetry limits.

## Install

The app is installable. `vite-plugin-pwa` (see `vite.config.ts`) generates the web manifest and a service worker that precaches the app shell; the theme and background colors come from `--surface` in `shared/design/tokens.css`. Icons live in `public/`.

The home screen shows an install banner: a native "Install Janus" button on Android/Chrome, and "Tap Share, then Add to Home Screen" on iOS. It is hidden when the app is already running installed. On iOS the installed app has its own storage, so install before adding panels.
