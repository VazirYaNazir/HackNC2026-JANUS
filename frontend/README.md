# Panel Builder Studio

The desktop Studio edits the same panel used by the PWA. Its widget palette,
logical canvas, inspector, preview, and undo/redo connect to the FastAPI backend
through the shared `@janus/client` workspace.

Install dependencies once with `npm ci` from the repository root. In a Linux
cloud workspace where only the browser renderer is needed, use
`ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci`; that does not install the Electron
runtime.

Choose **Connect backend** and supply the backend address and your laptop's
registered controller credential. The credential stays in window memory;
`VITE_BACKEND_URL` may provide a non-secret default address. Remote addresses
must use HTTPS.

Add/select a control and describe its action in the assistant. Execution
settings request an operation, target, capabilities, and platform libraries.
The connected laptop supplies the default target. The request dialog offers
similar previously validated programs and defaults to generating a new one.
Reuse requires selecting a suggestion and confirming. Every execution needs a
fresh locally granted request ID.

**Save & Push** saves the layout with revision checking and notifies paired
phones. **Pair Device** shows a QR and 60-second single-use code. **Requests**
shows live status, locally shared results, and cancellation. Loading a remotely
changed panel is explicit, preserving unsaved editor work.

See [connected panels](../backend/app/docs/connected-panels.md) for backend,
database, laptop authorization, deployment, and tests.

From `frontend/`, start the Electron Studio with one command:

```powershell
npm run electron
```

The command starts Vite, waits for the development server, opens Electron, and stops Vite when the window closes. Use `npm run dev` by itself when working in a browser.

Validate the production frontend with:

```powershell
npm run build
npm run lint
npm run test
```

For the production browser integration test, build both frontends from the
root, install Chromium with `npx playwright install chromium`, and run
`npm run test:e2e --workspace frontend`. It starts a temporary real backend and
an agent restricted to author-written C fixtures. Live Gemini and native OS
isolation are separate checks.
