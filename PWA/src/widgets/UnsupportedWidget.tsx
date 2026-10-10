import { Frame } from "./Frame";
import type { WidgetProps } from "./types";

export default function UnsupportedWidget(props: WidgetProps) {
  return (
    <Frame props={props} selectable className="jw--unsupported">
      <span className="jw__title">Unsupported widget</span>
      <span className="jw__hint">{String(props.widget.type)}</span>
    </Frame>
  );
}
