import type { CSSProperties } from "react";
import { logicalRectStyle, normalizeLayout } from "@janus/design";
import type { Layout } from "@janus/schema";
import WidgetView from "./WidgetView";
import { isInputWidget, type WidgetProps, type WidgetState, type WidgetValue } from "./types";
import "./widgets.css";

export interface PanelGridProps {
  layout: Layout;
  mode?: "edit" | "live";
  /** Current value of stateful inputs (toggle, slider), keyed by widget id. */
  values?: Record<string, WidgetValue>;
  /** Live readings for dials, keyed by `source`. */
  data?: Record<string, number | string>;
  /** Run state (or the agent's reply) of inputs, keyed by widget id. */
  states?: Record<string, WidgetState>;
  selectedId?: string | null;
  onSelect?: WidgetProps["onSelect"];
  onTrigger?: WidgetProps["onTrigger"];
}

/** Renders both legacy grid layouts and version 2 logical layouts through one normalized canvas. */
export default function PanelGrid({
  layout,
  mode = "live",
  values = {},
  data = {},
  states = {},
  selectedId = null,
  onSelect,
  onTrigger,
}: PanelGridProps) {
  let normalized;
  try {
    normalized = normalizeLayout(layout);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid layout.";
    return <div className="jw-grid-error" role="alert">{message}</div>;
  }

  const canvasStyle: CSSProperties = {
    aspectRatio: `${normalized.canvas.width} / ${normalized.canvas.height}`,
  };

  return (
    <div className="jw-grid" style={canvasStyle}>
      {normalized.widgets.map((widget) => (
        <div
          key={widget.id}
          className="jw-cell"
          style={logicalRectStyle(widget) as CSSProperties}
        >
          <WidgetView
            widget={widget}
            mode={mode}
            value={isInputWidget(widget) ? values[widget.id] : data[widget.source]}
            state={states[widget.id]}
            selected={selectedId === widget.id}
            onSelect={onSelect}
            onTrigger={onTrigger}
          />
        </div>
      ))}
    </div>
  );
}
