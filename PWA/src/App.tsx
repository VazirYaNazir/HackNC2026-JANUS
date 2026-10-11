import { lazy, Suspense, useEffect, useState } from "react";
import type { PairQR, SavedPanel } from "@janus/schema";
import { parsePairingLink } from "@janus/client";
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

type Screen = { name: "home" } | { name: "scan"; repair?: SavedPanel; target?: PairQR; method?: "scan" | "manual" } | { name: "panel"; panel: SavedPanel };

export default function App() {
  const gallery = isGallery();
  const [screen, setScreen] = useState<Screen>(() => {
    const target = parsePairingLink(window.location.href);
    return target ? { name: "scan", target, method: "manual" } : { name: "home" };
  });

  useEffect(() => {
    // Keep a short-lived code out of bookmarks and reloads after opening a PWA link.
    const url = new URL(window.location.href);
    if (url.searchParams.has("pairing_code")) {
      url.searchParams.delete("pairing_code");
      url.searchParams.delete("backend");
      window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
    }
  }, []);

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
          initialTarget={screen.target}
          method={screen.method}
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
        <HomeScreen onOpen={open} onScan={() => setScreen({ name: "scan" })} onManual={() => setScreen({ name: "scan", method: "manual" })} onRepair={(repair) => setScreen({ name: "scan", repair, method: "manual" })} />
      )}
      </Suspense>
    </>
  );
}
