import { Frame } from "./Frame";
import Icon from "./Icon";
import { activate, type WidgetProps } from "./types";

export default function ButtonWidget(props: WidgetProps) {
  const { widget, state } = props;
  return (
    <Frame props={props}>
      <button type="button" className="jw__tap jw__tap--amber" onClick={() => activate(props)}>
        <Icon name={widget.icon} />
        <span className="jw__title">{widget.label}</span>
        {state === "running" && <span className="jw__hint">Running…</span>}
        {state === "done" && <span className="jw__hint jw__hint--green">Done</span>}
        {state === "error" && <span className="jw__hint jw__hint--coral">Failed</span>}
      </button>
    </Frame>
  );
}
