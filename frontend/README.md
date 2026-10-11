# Panel Builder Studio

Milestone 1 is the structural Electron shell: a top toolbar, empty sidebars, an empty centered phone frame, and an empty bottom toolbar. The interface is intentionally static and light. No editor behavior, widget catalog, data, themes, or persistence is included yet.

From `frontend/`, start the Electron Studio with one command:

```powershell
npm run electron
```

The command starts Vite, waits for the development server, opens Electron, and stops Vite when the window closes. Use `npm run dev` by itself when working in a browser.

Validate the production frontend with:

```powershell
npm run build
npm run lint
```
