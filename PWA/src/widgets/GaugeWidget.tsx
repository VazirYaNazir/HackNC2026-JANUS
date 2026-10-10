import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { isDialWidget, type WidgetProps } from "./types";
import { levelOf } from "./level";

export default function GaugeWidget(props: WidgetProps) {
  const { widget, value } = props;
  const reading = typeof value === "number" ? value : undefined;
  const pct = reading === undefined ? 0 : Math.min(100, Math.max(0, reading));
  const unit = isDialWidget(widget) ? (widget.unit ?? "") : "";
  const level = levelOf(widget, reading);

  return (
    <Frame props={props} selectable>
      <WidgetTitle widget={widget} />
      <div className="jw__gauge">
        <svg viewBox="0 0 100 58" role="img" aria-label={`${widget.label} ${reading ?? "no data"}${unit}`}>
          <path className="jw__arc jw__arc--track" d="M 10 52 A 40 40 0 0 1 90 52" pathLength={100} />
          <path
            className={`jw__arc jw__arc--fill jw__arc--${level}`}
            d="M 10 52 A 40 40 0 0 1 90 52"
            pathLength={100}
            strokeDasharray={`${pct} 100`}
          />
        </svg>
        <span className="jw__big">
          {reading === undefined ? "—" : Math.round(reading)}
          <small>{unit}</small>
        </span>
      </div>
    </Frame>
  );
}
