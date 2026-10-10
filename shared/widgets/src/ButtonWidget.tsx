import { Icon } from "@janus/design";
import { Frame } from "./Frame";
import { activate, type WidgetProps } from "./types";

export default function ButtonWidget(props: WidgetProps) {
  const { widget, state } = props;
  return (
    <Frame props={props}>
      <button
        type="button"
        className="jw__tap jw__tap--amber"
        aria-label={widget.label}
        title={widget.label}
        onClick={() => activate(props)}
      >
        {widget.icon && <Icon name={widget.icon} className="jw__ico" />}
        <span className="jw__label">{widget.label}</span>
        {state === "running" && <span className="jw__hint">Running…</span>}
        {state === "done" && <span className="jw__hint jw__hint--green">Done</span>}
        {state === "error" && <span className="jw__hint jw__hint--coral">Failed</span>}
      </button>
    </Frame>
  );
}
