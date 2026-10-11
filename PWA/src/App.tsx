import { lazy, Suspense, useState } from "react";
import type { SavedPanel } from "@janus/schema";
import HomeScreen from "./screens/HomeScreen";
import PanelScreen from "./screens/PanelScreen";
import { touchPanel } from "./lib/panels";
import "./ui.css";

// The widget gallery is a dev tool: reachable only by URL (/?gallery) on the local dev server, with no link
// to it in the app. In a production build `Gallery` is null and the import is dropped from the bundle.
const Gallery = import.meta.env.DEV ? lazy(() => import("./screens/Gallery")) : null;
const isGallery = () =>
  Gallery !== null &&
  (new URLSearchParams(window.location.search).has("gallery") || window.location.pathname.endsWith("/gallery"));

// Loaded on demand: the QR scanner is the heaviest dependency.
const ScanScreen = lazy(() => import("./screens/ScanScreen"));

type Screen = { name: "home" } | { name: "scan"; repair?: SavedPanel } | { name: "panel"; panel: SavedPanel };

export default function App() {
  const gallery = isGallery();
  const [screen, setScreen] = useState<Screen>({ name: "home" });

  const home = () => setScreen({ name: "home" });
  const open = (panel: SavedPanel) => {
    const updated = touchPanel(panel.id).find((p) => p.id === panel.id) ?? panel;
    setScreen({ name: "panel", panel: updated });
  };

  return (
    <>
      <Suspense fallback={null}>
      {gallery && Gallery ? (
        <Gallery />
      ) : screen.name === "scan" ? (
        <ScanScreen
          repairing={screen.repair}
          onDone={(panel) => {
            const back = panel ?? screen.repair;
            if (back) setScreen({ name: "panel", panel: back });
            else home();
          }}
        />
      ) : screen.name === "panel" ? (
        <PanelScreen
          key={screen.panel.id}
          panel={screen.panel}
          onBack={home}
          onRepair={() => setScreen({ name: "scan", repair: screen.panel })}
        />
      ) : (
        <HomeScreen onOpen={open} onScan={() => setScreen({ name: "scan" })} />
      )}
      </Suspense>
    </>
  );
}
