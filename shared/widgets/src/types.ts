import { isInput, type DialWidget, type InputWidget, type RunState, type Widget } from "@janus/schema";

export type WidgetValue = number | string | boolean;

export interface WidgetProps {
  widget: Widget;
  mode: "edit" | "live";
  value?: WidgetValue;
  state?: RunState;
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
