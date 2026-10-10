import type { CSSProperties } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { asNumber, formatNumber } from "./format";
import { levelOf } from "./level";
import { isDialWidget, type WidgetProps } from "./types";

export default function TemperatureWidget(props: WidgetProps) {
  const { widget, value } = props;
  const reading = asNumber(value);
  const pct = reading === undefined ? 0 : Math.min(100, Math.max(0, reading));
  const unit = isDialWidget(widget) ? (widget.unit ?? "°C") : "°C";
  const level = levelOf(widget, reading, { warn: 70, critical: 85 });

  return (
    <Frame props={props} selectable>
      <div className="jw__thermo" role="img" aria-label={`${widget.label} ${reading === undefined ? "no data" : formatNumber(reading)}${unit}`}>
        <div className="jw__tube">
          <div className={`jw__mercury jw__mercury--${level}`} style={{ "--pct": `${pct}%` } as CSSProperties} />
        </div>
        <div className={`jw__bulb jw__bulb--${level}`} />
      </div>
      <WidgetTitle widget={widget} />
      <span className={`jw__val jw__val--${level}`}>
        {reading === undefined ? "—" : Math.round(reading)}
        <small>{unit}</small>
      </span>
    </Frame>
  );
}
