import { useState } from "react";
import type { SavedPanel } from "@janus/schema";
import Gallery from "./screens/Gallery";
import HomeScreen from "./screens/HomeScreen";
import PanelScreen from "./screens/PanelScreen";
import ScanScreen from "./screens/ScanScreen";
import { touchPanel } from "./lib/panels";
import { mockData, mockLayout, mockValues } from "./lib/mockLayout";
import "./ui.css";

const isGallery = () =>
  new URLSearchParams(window.location.search).has("gallery") || window.location.pathname.endsWith("/gallery");

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
      {gallery ? (
        <Gallery />
      ) : screen.name === "scan" ? (
        <ScanScreen onDone={home} />
      ) : screen.name === "panel" ? (
        <PanelScreen panel={screen.panel} layout={mockLayout} data={mockData} initialValues={mockValues} onBack={home} />
      ) : (
        <HomeScreen onOpen={open} onScan={() => setScreen({ name: "scan" })} />
      )}
    </>
  );
}
