import type { CSSProperties } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { asNumber, formatNumber } from "./format";
import { levelOf } from "./level";
import { isDialWidget, type WidgetProps } from "./types";

export default function BigNumberWidget(props: WidgetProps) {
  const { widget, value } = props;
  const reading = asNumber(value);
  const unit = isDialWidget(widget) ? (widget.unit ?? "") : "";
  const level = levelOf(widget, reading);
  const text = reading !== undefined ? formatNumber(reading) : typeof value === "string" ? value : "—";

  return (
    <Frame props={props} selectable>
      <WidgetTitle widget={widget} />
      <span
        className={`jw__val jw__val--${level}`}
        style={{ "--len": text.length, "--len-u": text.length + unit.length } as CSSProperties}
        aria-label={`${widget.label} ${text}${unit}`}
      >
        {text}
        <small>{unit}</small>
      </span>
    </Frame>
  );
}
