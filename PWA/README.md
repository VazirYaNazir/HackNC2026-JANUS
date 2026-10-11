# @janus/pwa

The phone panel app: React + Vite + TypeScript. Part of the npm workspace at the repo root.

## Develop

```
npm install            # from the repo root, never in this folder
cd PWA && npm run dev -- --host
```

The dev server runs over https (self-signed) so the camera works on a phone. Accept the certificate warning once.

Typecheck: `npx tsc -b`. Lint: `npm run lint`. Production build: `npm run build` (output in `dist/`).

## Deploy to Vercel

1. Import the repo and set **Root Directory** to `PWA`.
2. Leave **Include source files outside of the Root Directory** enabled. The app imports `shared/*` (`@janus/design`, `@janus/schema`, `@janus/widgets`), which live outside `PWA/`.
3. Everything else comes from `vercel.json`: install from the repo root (`npm ci`), build with `npm run build`, serve `dist/`, and rewrite unknown paths to `index.html`.
4. Use Node 20.19 or newer (Project Settings, Node.js Version).

There are no environment variables yet. When the real pairing arrives, the PC's host and token come from the QR code, not from build-time config.

## Install

The app is installable. `vite-plugin-pwa` (see `vite.config.ts`) generates the web manifest and a service worker that precaches the app shell; the theme and background colors come from `--surface` in `shared/design/tokens.css`. Icons live in `public/`.

The home screen shows an install banner: a native "Install Janus" button on Android/Chrome, and "Tap Share, then Add to Home Screen" on iOS. It is hidden when the app is already running installed. On iOS the installed app has its own storage, so install before adding panels.
