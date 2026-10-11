import { lazy, Suspense, useState } from "react";
import type { SavedPanel } from "@janus/schema";
import HomeScreen from "./screens/HomeScreen";
import PanelScreen from "./screens/PanelScreen";
import { touchPanel } from "./lib/panels";
import { mockData, mockLayout, mockValues } from "./lib/mockLayout";
import "./ui.css";

const isGallery = () =>
  new URLSearchParams(window.location.search).has("gallery") || window.location.pathname.endsWith("/gallery");

// Loaded on demand: the QR scanner is the heaviest dependency, and the gallery is a dev tool.
const Gallery = lazy(() => import("./screens/Gallery"));
const ScanScreen = lazy(() => import("./screens/ScanScreen"));

type Screen = { name: "home" } | { name: "scan" } | { name: "panel"; panel: SavedPanel };

export default function App() {
  const gallery = isGallery();
  const [screen, setScreen] = useState<Screen>({ name: "home" });

  const home = () => setScreen({ name: "home" });
  const open = (panel: SavedPanel) => {
    // Every saved panel shows the mock layout until the PC serves real ones.
    const updated = touchPanel(panel.id).find((p) => p.id === panel.id) ?? panel;
    setScreen({ name: "panel", panel: updated });
  };

  return (
    <>
      {import.meta.env.DEV && (
        <nav className="app-nav">
          <a href="/" aria-current={gallery ? undefined : "page"}>App</a>
          <a href="/?gallery" aria-current={gallery ? "page" : undefined}>Gallery</a>
        </nav>
      )}
      <Suspense fallback={null}>
      {gallery ? (
        <Gallery />
      ) : screen.name === "scan" ? (
        <ScanScreen onDone={home} />
      ) : screen.name === "panel" ? (
        <PanelScreen panel={screen.panel} layout={mockLayout} data={mockData} initialValues={mockValues} onBack={home} />
      ) : (
        <HomeScreen onOpen={open} onScan={() => setScreen({ name: "scan" })} />
      )}
      </Suspense>
    </>
  );
}
