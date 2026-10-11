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

The app is installable (web manifest and icons in `public/`). There is no service worker, so it needs a connection to work.
