import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { asNumber } from "./format";
import { levelOf } from "./level";
import { isDialWidget, type WidgetProps } from "./types";

/* Full circle, drawn from 12 o'clock clockwise. */
const CIRCLE = "M 50 10 A 40 40 0 1 1 50 90 A 40 40 0 1 1 50 10";

export default function RingWidget(props: WidgetProps) {
  const { widget, value } = props;
  const reading = asNumber(value);
  const pct = reading === undefined ? 0 : Math.min(100, Math.max(0, reading));
  const unit = isDialWidget(widget) ? (widget.unit ?? "") : "";
  const level = levelOf(widget, reading, { warn: 70, critical: 90 });

  return (
    <Frame props={props} selectable>
      <WidgetTitle widget={widget} />
      <div className="jw__ringbox">
        <div className="jw__ring">
          <svg viewBox="0 0 100 100" role="img" aria-label={`${widget.label} ${reading ?? "no data"}${unit}`}>
            <path className="jw__arc jw__arc--groove-dark" d={CIRCLE} pathLength={100} />
            <path className="jw__arc jw__arc--groove-light" d={CIRCLE} pathLength={100} />
            <path className="jw__arc jw__arc--track" d={CIRCLE} pathLength={100} />
            {pct > 0 && (
              <path
                className={`jw__arc jw__arc--fill jw__arc--${level}`}
                d={CIRCLE}
                pathLength={100}
                strokeDasharray={`${pct} 100`}
              />
            )}
          </svg>
          <div className="jw__dial" aria-hidden="true">
            <span className="jw__big">
              {reading === undefined ? "—" : Math.round(reading)}
              <small>{unit}</small>
            </span>
          </div>
        </div>
      </div>
    </Frame>
  );
}
