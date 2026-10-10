import type { CSSProperties } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { isDialWidget, type WidgetProps } from "./types";
import { levelOf } from "./level";

export default function BarWidget(props: WidgetProps) {
  const { widget, value } = props;
  const reading = typeof value === "number" ? value : undefined;
  const pct = reading === undefined ? 0 : Math.min(100, Math.max(0, reading));
  const unit = isDialWidget(widget) ? (widget.unit ?? "") : "";

  return (
    <Frame props={props} selectable>
      <div className="jw__row">
        <WidgetTitle widget={widget} />
        <span className="jw__readout">
          {reading === undefined ? "—" : Math.round(reading)}
          {unit}
        </span>
      </div>
      <div
        className="jw__track"
        role="meter"
        aria-label={widget.label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={reading}
      >
        <div className={`jw__fill jw__fill--${levelOf(widget, reading)}`} style={{ "--pct": `${pct}%` } as CSSProperties} />
      </div>
    </Frame>
  );
}
