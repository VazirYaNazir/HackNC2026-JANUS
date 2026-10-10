import { useState } from "react";
import { GRID } from "@janus/design";
import { isInput, SCHEMA_VERSION, type Layout, type Widget, type WidgetType } from "@janus/schema";
import { PanelGrid, WIDGETS, WIDGET_TYPES, type WidgetValue } from "@janus/widgets";
import "./Gallery.css";

const ICONS: Partial<Record<WidgetType, string>> = {
  button: "save",
  toggle: "music",
  slider: "volume",
  bar: "memory",
  gauge: "cpu",
};
const LABELS = [
  { key: "short", text: "CPU" },
  { key: "long", text: "A very long widget label that cannot fit" },
] as const;
const READINGS: Record<string, number> = { "demo.short": 42, "demo.long": 83 };

/** Places widgets left-to-right, wrapping at the column count. Returns the laid-out widgets. */
function section(type: WidgetType, w: number, h: number): Layout {
  let x = 0;
  let y = 0;
  const widgets = LABELS.map(({ key, text }): Widget => {
    if (x + w > GRID.columns) {
      x = 0;
      y += h;
    }
    const base = { id: `${type}-${w}x${h}-${key}`, label: text, icon: ICONS[type], x, y, w, h };
    x += w;
    return isInput(type)
      ? { ...base, type, action: "gallery.demo", min: 0, max: 100, step: 1 }
      : { ...base, type, source: `demo.${key}`, unit: "%", warn: 70, critical: 90 };
  });
  return { schemaVersion: SCHEMA_VERSION, name: `${type} ${w}x${h}`, columns: GRID.columns, widgets };
}

export default function Gallery() {
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
                data={READINGS}
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
