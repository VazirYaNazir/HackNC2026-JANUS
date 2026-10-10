import { Frame } from "./Frame";
import { activate, type WidgetProps } from "./types";

export default function ToggleWidget(props: WidgetProps) {
  const { widget, value } = props;
  const on = value === true;
  return (
    <Frame props={props}>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        className="jw__tap jw__tap--row"
        onClick={() => activate(props, !on)}
      >
        <span className="jw__title">{widget.label}</span>
        <span className={`jw__switch ${on ? "jw__switch--on" : ""}`} aria-hidden="true">
          <span className="jw__knob" />
        </span>
      </button>
    </Frame>
  );
}
