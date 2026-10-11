import { LEGACY_SCHEMA_VERSION, type Layout } from "@janus/schema";
import { GRID } from "@janus/design";

export const mockLayout: Layout = {
  schemaVersion: LEGACY_SCHEMA_VERSION,
  name: "Desk Control",
  columns: GRID.columns,
  widgets: [
    { id: "w1", type: "button", label: "Run Backup", icon: "save", x: 0, y: 0, w: 2, h: 1, action: "backup.run" },
    { id: "w2", type: "toggle", label: "Spotify", icon: "music", x: 2, y: 0, w: 2, h: 1, action: "spotify.toggle" },
    { id: "w3", type: "gauge", label: "CPU", icon: "cpu", x: 0, y: 1, w: 2, h: 2, source: "cpu.percent", unit: "%", warn: 70, critical: 90 },
    { id: "w4", type: "bar", label: "Memory", icon: "memory", x: 2, y: 1, w: 2, h: 1, source: "mem.percent", unit: "%", warn: 75, critical: 90 },
    { id: "w5", type: "slider", label: "Volume", icon: "volume", x: 2, y: 2, w: 2, h: 1, action: "volume.set", min: 0, max: 100, step: 1 },
  ],
};

/** Live readings keyed by DialWidget.source. */
export const mockData: Record<string, number> = {
  "cpu.percent": 42,
  "mem.percent": 68,
};

/** Starting state for stateful inputs, keyed by widget id. */
export const mockValues: Record<string, number | boolean> = {
  w2: false,
  w5: 60,
};
