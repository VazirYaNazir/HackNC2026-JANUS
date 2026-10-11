import { useEffect, useRef, useState } from "react";
import { GRID } from "@janus/design";
import { isInput, LEGACY_SCHEMA_VERSION, type Layout, type Widget, type WidgetType } from "@janus/schema";
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
  stream: "monitor",
  joystick: "monitor",
  dpad: "play",
  knob: "sun",
  command: "terminal",
  agent: "sparkle",
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
  stream: "Screen",
  joystick: "Mouse",
  dpad: "Arrows",
  knob: "Brightness",
  command: "Run command",
  agent: "Gemini",
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
const MOCK_SHORT_REPLY = "Your next meeting is at 3pm.";
const MOCK_LONG_REPLY = [
  "Here is a long answer from the mock agent, so the reply area has to scroll.",
  "First, check that the backup drive is mounted. Then run the backup script and wait for it to report that every folder has synced.",
  "If anything fails, open the agent log for the exact error, fix the path it names, and run the script again.",
  "Finally, confirm the newest backup shows up in the destination folder with today's date.",
].join(" ");
/** Mock host frame: an SVG "desktop" with a moving cursor and a clock. Real frames would come from the PC. */
function mockFrame(n: number): string {
  const x = 40 + ((n * 23) % 220);
  const y = 30 + ((n * 17) % 100);
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180"><rect width="320" height="180" fill="#2a3040"/>` +
    `<rect x="16" y="16" width="170" height="110" rx="6" fill="#1a1f2b"/><rect x="16" y="16" width="170" height="14" rx="6" fill="#3b4358"/>` +
    `<text x="26" y="62" fill="#5ec8e5" font-family="monospace" font-size="11">npm run dev</text>` +
    `<text x="26" y="80" fill="#8a93a6" font-family="monospace" font-size="11">frame ${n}</text>` +
    `<rect y="164" width="320" height="16" fill="#1a1f2b"/><text x="266" y="176" fill="#8a93a6" font-family="monospace" font-size="10">${new Date().toLocaleTimeString([], { hour12: false })}</text>` +
    `<path d="M${x} ${y}l0 14 4-4 3 7 3-1-3-7 5 0z" fill="#fff"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}
const sourceOf = (type: WidgetType, key: Key) => `demo.${type}.${key}`;

let frameCount = 0;

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
      } else if (type === "stream") {
        // "short" streams; "long" points at a dead URL to show the offline state.
        next[id] = key === "short" ? mockFrame(++frameCount) : "/no-such-stream.mjpg";
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
  // Toggles also come in a push-down variant (stays pushed in while on).
  const entries = labels.flatMap((l) => (type === "toggle" ? [{ ...l, push: false }, { ...l, push: true }] : [{ ...l, push: false }]));
  const widgets = entries.map(({ key, text, push }): Widget => {
    if (x + w > GRID.columns) {
      x = 0;
      y += h;
    }
    const base = { id: `${type}-${w}x${h}-${key}${push ? "-push" : ""}`, label: text, icon: ICONS[type], x, y, w, h };
    x += w;
    return isInput(type)
      ? { ...base, type, action: "gallery.demo", min: 0, max: 100, step: 1, ...(push && { variant: "push" as const }) }
      : { ...base, type, source: sourceOf(type, key), unit: UNITS[type], ...THRESHOLDS[type] };
  });
  return { schemaVersion: LEGACY_SCHEMA_VERSION, name: `${type} ${w}x${h}`, columns: GRID.columns, widgets };
}

export default function Gallery() {
  const data = useTicker();
  const [states, setStates] = useState<Record<string, string>>(() => ({
    "agent-4x2-short": MOCK_SHORT_REPLY,
    "agent-4x2-long": MOCK_LONG_REPLY,
  }));
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  // ?edit renders every widget in edit mode: taps select, nothing triggers.
  const edit = new URLSearchParams(window.location.search).has("edit");
  const [lastInput, setLastInput] = useState<string>("none yet");
  const [values, setValues] = useState<Record<string, WidgetValue>>(() => {
    const initial: Record<string, WidgetValue> = {};
    for (const type of WIDGET_TYPES) {
      for (const { w, h } of WIDGETS[type]?.sizes ?? []) {
        if (type === "toggle") {
          initial[`toggle-${w}x${h}-long`] = true;
          initial[`toggle-${w}x${h}-long-push`] = true;
        }
        if (type === "knob") {
          initial[`knob-${w}x${h}-short`] = 40;
          initial[`knob-${w}x${h}-long`] = 75;
        }
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
      <p className="gallery__last" aria-live="off">Last input: {lastInput}</p>
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
                mode={edit ? "edit" : "live"}
                values={values}
                data={data}
                states={states}
                onSelect={(id) => setLastInput(`select ${id}`)}
                onTrigger={(widget, value) => {
                  setLastInput(`${widget.id} ${JSON.stringify(value)}`);
                  if (widget.type === "agent" && typeof value === "string") {
                    // Mock agent: "thinking" for a moment, then a reply.
                    setStates((prev) => ({ ...prev, [widget.id]: "running" }));
                    timers.current.push(
                      window.setTimeout(() => {
                        const reply = widget.id.endsWith("long") ? MOCK_LONG_REPLY : `You asked: ${value}`;
                        setStates((prev) => ({ ...prev, [widget.id]: reply }));
                      }, 800),
                    );
                  }
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
