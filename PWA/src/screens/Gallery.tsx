import { useEffect, useState } from "react";
import { GRID } from "@janus/design";
import { isInput, SCHEMA_VERSION, type Layout, type Widget, type WidgetType } from "@janus/schema";
import { PanelGrid, WIDGETS, WIDGET_TYPES, type WidgetValue } from "@janus/widgets";
import "./Gallery.css";

type Key = "short" | "long";
type Sample = number | string;

const ICONS: Partial<Record<WidgetType, string>> = {
  button: "save",
  toggle: "music",
  slider: "volume",
  bar: "memory",
  gauge: "cpu",
  line: "wifi",
  ring: "folder",
  temperature: "thermometer",
  number: "monitor",
  log: "terminal",
};
const SHORT_LABELS: Partial<Record<WidgetType, string>> = {
  button: "Backup",
  toggle: "Spotify",
  slider: "Volume",
  bar: "Memory",
  gauge: "CPU",
  line: "Network",
  ring: "Disk",
  temperature: "GPU Temp",
  number: "Processes",
  log: "Agent Log",
};
const LONG_LABEL = "A very long widget label that cannot fit";
const UNITS: Partial<Record<WidgetType, string>> = { gauge: "%", bar: "%", ring: "%", line: "%", number: "req/s" };
const THRESHOLDS: Partial<Record<WidgetType, { warn: number; critical: number }>> = {
  gauge: { warn: 70, critical: 90 },
  bar: { warn: 70, critical: 90 },
  ring: { warn: 70, critical: 90 },
  line: { warn: 70, critical: 90 },
  temperature: { warn: 70, critical: 85 },
};

/* ---------- Mock data ticker ---------- */

/** Range each dial wanders in. The "long" samples sit higher so the warn/critical colors show up. */
const RANGES: Partial<Record<WidgetType, Record<Key, [number, number]>>> = {
  gauge: { short: [25, 60], long: [75, 98] },
  bar: { short: [25, 60], long: [75, 98] },
  ring: { short: [25, 60], long: [75, 98] },
  line: { short: [20, 70], long: [60, 98] },
  temperature: { short: [38, 62], long: [72, 92] },
  number: { short: [0, 100], long: [900, 9999] },
};
const MESSAGES = [
  "backup started",
  "synced 12 files",
  "volume set to 60",
  "spotify: next track",
  "cpu governor: performance",
  "agent heartbeat ok",
  "screenshot saved",
  "wifi signal -52 dBm",
];
const KEYS: Key[] = ["short", "long"];
const sourceOf = (type: WidgetType, key: Key) => `demo.${type}.${key}`;

function tick(prev: Record<string, Sample>): Record<string, Sample> {
  const next: Record<string, Sample> = {};
  for (const type of WIDGET_TYPES) {
    for (const key of KEYS) {
      const id = sourceOf(type, key);
      const range = RANGES[type]?.[key];
      if (range) {
        const [lo, hi] = range;
        const last = prev[id];
        const base = typeof last === "number" ? last : lo + Math.random() * (hi - lo);
        const value = Math.min(hi, Math.max(lo, base + (Math.random() - 0.5) * (hi - lo) * 0.25));
        next[id] = Math.round(value * 10) / 10;
      } else if (type === "log") {
        const time = new Date().toLocaleTimeString([], { hour12: false });
        next[id] = `${time} ${MESSAGES[Math.floor(Math.random() * MESSAGES.length)]}`;
      }
    }
  }
  return next;
}

function useTicker(): Record<string, Sample> {
  const [data, setData] = useState(() => tick({}));
  useEffect(() => {
    const timer = setInterval(() => setData(tick), 1000);
    return () => clearInterval(timer);
  }, []);
  return data;
}

/* ---------- Layout ---------- */

/** One section: a short and a long sample of `type` at `w`x`h`, placed left to right. */
function section(type: WidgetType, w: number, h: number): Layout {
  let x = 0;
  let y = 0;
  const labels: { key: Key; text: string }[] = [
    { key: "short", text: SHORT_LABELS[type] ?? type },
    { key: "long", text: LONG_LABEL },
  ];
  const widgets = labels.map(({ key, text }): Widget => {
    if (x + w > GRID.columns) {
      x = 0;
      y += h;
    }
    const base = { id: `${type}-${w}x${h}-${key}`, label: text, icon: ICONS[type], x, y, w, h };
    x += w;
    return isInput(type)
      ? { ...base, type, action: "gallery.demo", min: 0, max: 100, step: 1 }
      : { ...base, type, source: sourceOf(type, key), unit: UNITS[type], ...THRESHOLDS[type] };
  });
  return { schemaVersion: SCHEMA_VERSION, name: `${type} ${w}x${h}`, columns: GRID.columns, widgets };
}

export default function Gallery() {
  const data = useTicker();
  const [values, setValues] = useState<Record<string, WidgetValue>>(() => {
    const initial: Record<string, WidgetValue> = {};
    for (const type of WIDGET_TYPES) {
      for (const { w, h } of WIDGETS[type]?.sizes ?? []) {
        if (type === "toggle") initial[`toggle-${w}x${h}-long`] = true;
        if (type === "slider") {
          initial[`slider-${w}x${h}-short`] = 30;
          initial[`slider-${w}x${h}-long`] = 70;
        }
      }
    }
    return initial;
  });

  return (
    <main className="gallery" style={{ padding: `${GRID.padding}px` }}>
      <h1 className="gallery__title">Widget gallery</h1>
      <p className="gallery__note">Dials update every second with mock data.</p>
      {WIDGET_TYPES.map((type) => (
        <section key={type} className="gallery__type">
          <h2>{type}</h2>
          {WIDGETS[type]?.sizes.map(({ w, h }) => (
            <div key={`${w}x${h}`} className="gallery__size">
              <h3>
                {w}×{h}
                {WIDGETS[type]?.defaultSize.w === w && WIDGETS[type]?.defaultSize.h === h && " (default)"}
              </h3>
              <PanelGrid
                layout={section(type, w, h)}
                values={values}
                data={data}
                onTrigger={(widget, value) => {
                  if (value !== undefined) setValues((prev) => ({ ...prev, [widget.id]: value }));
                }}
              />
            </div>
          ))}
        </section>
      ))}
    </main>
  );
}
