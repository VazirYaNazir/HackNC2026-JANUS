import { isInput, type DialWidget, type InputWidget, type Vec2, type Widget, type WidgetValue } from "@janus/schema";

export type { Vec2, WidgetValue };

/** A run state ("running" | "done" | "error"), or for the agent widget, the reply text. */
export type WidgetState = string;

export interface WidgetProps {
  widget: Widget;
  mode: "edit" | "live";
  value?: WidgetValue;
  state?: WidgetState;
  selected?: boolean;
  onSelect?: (id: string) => void;
  onTrigger?: (widget: Widget, value?: WidgetValue) => void;
}

export const isInputWidget = (w: Widget): w is InputWidget => isInput(w.type);
export const isDialWidget = (w: Widget): w is DialWidget => !isInput(w.type);

/** The only way a widget fires an action. In edit mode it selects instead, never triggers. */
export function activate(props: WidgetProps, value?: WidgetValue): void {
  if (props.mode === "edit") {
    props.onSelect?.(props.widget.id);
    return;
  }
  props.onTrigger?.(props.widget, value);
}
