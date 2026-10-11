import { isInput, type DialWidget, type InputWidget, type Vec2, type Widget, type WidgetValue } from "@janus/schema";

export type { Vec2, WidgetValue };

/** A run state ("running" | "done" | "error"), or for the agent widget, the reply text. */
export type WidgetState = string;

export interface WidgetProps {
  widget: Widget;
  mode: "edit" | "live";
  value?: WidgetValue;
  /** Last-hour trend for a dial's `source` (oldest first), when the backend has history for it. */
  history?: number[];
  state?: WidgetState;
  /** The backend reason for a failed request, separate from its run state. */
  error?: string;
  /** Native publisher status, including permission or capture failures. */
  telemetryStatus?: string;
  selected?: boolean;
  onSelect?: (id: string) => void;
  onTrigger?: (widget: Widget, value?: WidgetValue) => void | Promise<boolean>;
}

export const isInputWidget = (w: Widget): w is InputWidget => isInput(w.type);
export const isDialWidget = (w: Widget): w is DialWidget => !isInput(w.type);

/** The only way a widget fires an action. In edit mode it selects instead, never triggers. */
export function activate(props: WidgetProps, value?: WidgetValue): void | Promise<boolean> {
  if (props.mode === "edit") {
    props.onSelect?.(props.widget.id);
    return;
  }
  return props.onTrigger?.(props.widget, value);
}
