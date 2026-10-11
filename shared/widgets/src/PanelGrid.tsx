import { Fragment, type CSSProperties } from "react";
import { contentBottom, logicalRectStyle, normalizeLayout } from "@janus/design";
import type { Layout, Widget } from "@janus/schema";
import WidgetView from "./WidgetView";
import { isInputWidget, type WidgetProps, type WidgetState, type WidgetValue } from "./types";
import "./widgets.css";

export interface PanelGridProps {
  layout: Layout;
  mode?: "edit" | "live";
  /** Current value of stateful inputs (toggle, slider), keyed by widget id. */
  values?: Record<string, WidgetValue>;
  /** Live readings for dials, keyed by `source`. */
  data?: Record<string, number | string | null>;
  /** Last-hour history for dials, keyed by `source` (oldest first). Gauges draw it as a sparkline when present. */
  history?: Record<string, number[]>;
  /** Run state (or the agent's reply) of inputs, keyed by widget id. */
  states?: Record<string, WidgetState>;
  selectedId?: string | null;
  onSelect?: WidgetProps["onSelect"];
  onTrigger?: WidgetProps["onTrigger"];
  /**
   * View-only: inputs are dimmed and inert, and tapping one calls `onBlocked` instead of triggering anything.
   * Dials are unaffected. Ignored in edit mode.
   */
  readOnly?: boolean;
  onBlocked?: (widget: Widget) => void;
  /**
   * "canvas" (default) is the full logical canvas, as in the builder. "content" cuts the empty space below the
   * lowest widget. The width scale is the same either way, so every widget keeps the size it has in the builder.
   */
  fit?: "canvas" | "content";
}

/** Renders both legacy grid layouts and version 2 logical layouts through one normalized canvas. */
export default function PanelGrid({
  layout,
  mode = "live",
  values = {},
  data = {},
  history = {},
  states = {},
  selectedId = null,
  onSelect,
  onTrigger,
  readOnly = false,
  onBlocked,
  fit = "canvas",
}: PanelGridProps) {
  let normalized;
  try {
    normalized = normalizeLayout(layout);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid layout.";
    return <div className="jw-grid-error" role="alert">{message}</div>;
  }

  // Percent positions are relative to the box, so a shorter box needs positions measured against its own height.
  const canvas = fit === "content" ? { width: normalized.canvas.width, height: contentBottom(normalized.widgets) } : normalized.canvas;
  const canvasStyle: CSSProperties = canvas.height > 0 ? { aspectRatio: `${canvas.width} / ${canvas.height}` } : { height: 0 };

  return (
    <div className={`jw-grid${fit === "content" ? " jw-grid--fit" : ""}`} style={canvasStyle}>
      {normalized.widgets.map((widget) => {
        const rect = logicalRectStyle(widget, canvas) as CSSProperties;
        const blocked = readOnly && mode === "live" && isInputWidget(widget);
        return (
          <Fragment key={widget.id}>
            {/* inert removes the control from taps, focus and the accessibility tree; the button below takes over */}
            <div className={`jw-cell${blocked ? " jw-cell--blocked" : ""}`} style={rect} inert={blocked || undefined}>
              <WidgetView
                widget={widget}
                mode={mode}
                value={isInputWidget(widget) ? (widget.action === 'native_volume' ? data['volume.percent'] ?? values[widget.id] : values[widget.id]) : data[widget.source] ?? undefined}
                history={isInputWidget(widget) ? undefined : history[widget.source]}
                state={states[widget.id]}
                selected={selectedId === widget.id}
                onSelect={onSelect}
                onTrigger={blocked ? undefined : onTrigger}
              />
            </div>
            {blocked && (
              <button
                type="button"
                className="jw-blocked"
                style={rect}
                aria-label={`${widget.label} is unavailable right now`}
                onClick={() => onBlocked?.(widget)}
              />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}
