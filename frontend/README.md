# Panel Builder Studio

Milestone 1 is the structural Electron shell: a top toolbar, empty sidebars, an empty centered phone frame, and an empty bottom toolbar. The interface is intentionally static and light. No editor behavior, widget catalog, data, themes, or persistence is included yet.

From `frontend/`, run the Vite server and Electron app in separate terminals:

```powershell
npm run dev
npm run electron
```

Validate the production frontend with:

```powershell
npm run build
npm run lint
```
