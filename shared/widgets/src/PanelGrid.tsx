import type { CSSProperties } from "react";
import { GRID } from "@janus/design";
import type { Layout, RunState } from "@janus/schema";
import WidgetView from "./WidgetView";
import { isInputWidget, type WidgetProps, type WidgetValue } from "./types";
import "./widgets.css";

export interface PanelGridProps {
  layout: Layout;
  mode?: "edit" | "live";
  /** Current value of stateful inputs (toggle, slider), keyed by widget id. */
  values?: Record<string, WidgetValue>;
  /** Live readings for dials, keyed by `source`. */
  data?: Record<string, number | string>;
  /** Run state of inputs, keyed by widget id. */
  states?: Record<string, RunState>;
  selectedId?: string | null;
  onSelect?: WidgetProps["onSelect"];
  onTrigger?: WidgetProps["onTrigger"];
}

const gridStyle: CSSProperties = {
  display: "grid",
  alignContent: "start",
  gridTemplateColumns: `repeat(${GRID.columns}, minmax(0, 1fr))`,
  gridAutoRows: `${GRID.rowHeight}px`,
  gap: `${GRID.gap}px`,
};

/** Renders a Layout on the phone grid. Widget x/y/w/h are grid cells. */
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
  return (
    <div className="jw-grid" style={gridStyle}>
      {layout.widgets.map((widget) => (
        <div
          key={widget.id}
          className="jw-cell"
          style={{ gridColumn: `${widget.x + 1} / span ${widget.w}`, gridRow: `${widget.y + 1} / span ${widget.h}` }}
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
