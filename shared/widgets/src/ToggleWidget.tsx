import { Icon } from "@janus/design";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { activate, isInputWidget, type WidgetProps } from "./types";

export default function ToggleWidget(props: WidgetProps) {
  const { widget, value } = props;
  const on = value === true;

  if (isInputWidget(widget) && widget.variant === "push") {
    // Same layout as a button; the whole tile stays pushed in (inset) and turns green while on.
    return (
      <Frame props={props} typeClass="button" className={`jw--push ${on ? "jw--push-on" : ""}`}>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label={widget.label}
          title={widget.label}
          className="jw__tap"
          onClick={() => activate(props, !on)}
        >
          {widget.icon && <Icon name={widget.icon} className="jw__ico" />}
          <span className="jw__label">{widget.label}</span>
        </button>
      </Frame>
    );
  }

  return (
    <Frame props={props}>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={widget.label}
        className="jw__tap jw__tap--row"
        onClick={() => activate(props, !on)}
      >
        <WidgetTitle widget={widget} />
        <span className={`jw__switch ${on ? "jw__switch--on" : ""}`} aria-hidden="true">
          <span className="jw__knob" />
        </span>
      </button>
    </Frame>
  );
}
