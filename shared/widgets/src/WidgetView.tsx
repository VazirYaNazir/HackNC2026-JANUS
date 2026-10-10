import { createElement } from "react";
import { getWidgetComponent } from "./registry";
import type { WidgetProps } from "./types";

export default function WidgetView(props: WidgetProps) {
  return createElement(getWidgetComponent(props.widget.type), props);
}
