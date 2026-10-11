# Janus

Build custom widget panels on your PC, then use them from any phone. Tap a widget on the phone → something runs on the PC.

## Repo layout

- `PWA/` — phone panel, React + Vite + TypeScript, deployed to Vercel (`@janus/pwa`)
- `frontend/` — builder desktop app, Electron (`@janus/builder`), built by a teammate
- `backend/` — FastAPI (Python), runs actions on the PC
- `agent/` — agent code (not part of this task)
- `shared/design/` — `@janus/design`: tokens.css, tokens.ts, grid.ts
- `shared/schema/` — `@janus/schema`: types for layouts, pairing, WebSocket messages
- `shared/widgets/` — `@janus/widgets` (to be created): widget components + registry, shared by PWA and builder

npm workspaces at the root. Run `npm install` from the root, never inside a package for `@janus/*` deps.

## Rules

- Types come from `@janus/schema`. Don't redefine Layout, Widget, or message types locally. If a type needs to change, change it in `shared/schema/index.ts`.
- Styling uses tokens only: `var(--surface)`, `var(--amber)`, `var(--neu-raised)`, etc. from `@janus/design/tokens.css`. No hex colors in components.
- Grid placement uses `GRID` from `@janus/design` (4 columns, 62px rows, 12px gap). Widget `x/y/w/h` are grid cells.
- Color meaning: amber = inputs/actions, cyan = live data, green = on/online, coral = critical/delete.
- Neumorphic phone style: every widget uses the same surface color as the background; depth comes only from `--neu-raised` / `--neu-inset`. Active/pressed = inset.
- Widget props: `{ widget, mode: "edit" | "live", value?, state?, selected?, onSelect?, onTrigger? }`. In `mode="edit"` a widget must never call `onTrigger`.
- Unknown widget types render an "Unsupported widget" tile, never crash.
- Real `<button>` elements, `aria-label` on icon-only controls, touch targets ≥ 44px.

## Backend (current state)

- `GET /api/v1/panels/widgets` → `{ widgets: [{ script_id, name, widget: { widget_type, label } }] }`
- `POST /api/v1/panels/run/{script_id}` → runs the script, returns `{ status, returncode, stdout, stderr }`
- Auth: `Authorization: Bearer <token>`
- No layout positions or WebSocket yet. The PWA converts responses into the `Layout` type in `PWA/src/lib/adapter.ts`, so widgets don't depend on the backend's field names.

## PWA structure

```
PWA/src/
  main.tsx, App.tsx
  lib/connection.ts   host + token (localStorage), fetch helpers
  lib/adapter.ts      backend response → Layout
  lib/mockLayout.ts   sample "Desk Control" layout
  screens/PanelScreen.tsx
  screens/PairScreen.tsx
```

## Commands

- Dev (reachable from a phone on the same Wi-Fi): `cd PWA && npm run dev -- --host`
- Typecheck: `cd PWA && npx tsc -b`
- Lint: `cd PWA && npm run lint`

Done means typecheck and lint pass.