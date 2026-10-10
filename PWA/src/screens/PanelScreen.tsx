import { useState, type CSSProperties } from "react";
import { GRID } from "@janus/design";
import type { Layout, Widget } from "@janus/schema";
import WidgetView from "../widgets/WidgetView";
import { isInputWidget, type WidgetValue } from "../widgets/types";
import "./PanelScreen.css";

interface PanelScreenProps {
  layout: Layout;
  /** Live readings keyed by DialWidget.source. */
  data?: Record<string, number | string>;
  /** Starting values for stateful inputs, keyed by widget id. */
  initialValues?: Record<string, WidgetValue>;
}

export default function PanelScreen({ layout, data = {}, initialValues = {} }: PanelScreenProps) {
  const [values, setValues] = useState<Record<string, WidgetValue>>(initialValues);

  const handleTrigger = (widget: Widget, value?: WidgetValue) => {
    if (!isInputWidget(widget)) return;
    console.log("[janus] trigger", { widget: widget.id, action: widget.action, value });
    if (value !== undefined) setValues((prev) => ({ ...prev, [widget.id]: value }));
  };

  const gridStyle: CSSProperties = {
    gridTemplateColumns: `repeat(${GRID.columns}, minmax(0, 1fr))`,
    gridAutoRows: `${GRID.rowHeight}px`,
    gap: `${GRID.gap}px`,
    alignContent: "start",
  };

  return (
    <main className="panel" style={{ padding: `${GRID.padding}px` }}>
      <h1 className="panel__title">{layout.name}</h1>
      <div className="panel__grid" style={gridStyle}>
        {layout.widgets.map((widget) => {
          const value = isInputWidget(widget) ? values[widget.id] : data[widget.source];
          return (
            <div
              key={widget.id}
              style={{ gridColumn: `${widget.x + 1} / span ${widget.w}`, gridRow: `${widget.y + 1} / span ${widget.h}` }}
            >
              <WidgetView widget={widget} mode="live" value={value} onTrigger={handleTrigger} />
            </div>
          );
        })}
      </div>
    </main>
  );
}
