import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { asNumber, formatNumber } from "./format";
import { levelOf } from "./level";
import { isDialWidget, type WidgetProps } from "./types";
import { useHistory } from "./useHistory";

const MAX_POINTS = 60;

/** Maps samples onto a 100x100 box, newest on the right, with the y range fitted to the data. */
function plot(samples: number[]) {
  const lo = Math.min(...samples);
  const hi = Math.max(...samples);
  const span = Math.max(hi - lo, 1);
  const pts = samples.map((v, i) => {
    const x = ((MAX_POINTS - samples.length + i) / (MAX_POINTS - 1)) * 100;
    const y = 90 - ((v - lo) / span) * 80;
    return `${x.toFixed(2)} ${y.toFixed(2)}`;
  });
  const line = `M ${pts.join(" L ")}`;
  const first = pts[0].split(" ")[0];
  const last = pts[pts.length - 1].split(" ")[0];
  return { line, area: `${line} L ${last} 100 L ${first} 100 Z` };
}

export default function LineGraphWidget(props: WidgetProps) {
  const { widget, value } = props;
  const reading = asNumber(value);
  const samples = useHistory(reading, MAX_POINTS);
  const unit = isDialWidget(widget) ? (widget.unit ?? "") : "";
  const level = levelOf(widget, reading);
  const shape = samples.length > 1 ? plot(samples) : null;

  return (
    <Frame props={props} selectable>
      <WidgetTitle widget={widget} />
      <span className={`jw__val jw__val--${level}`}>
        {reading === undefined ? "—" : formatNumber(reading)}
        <small>{unit}</small>
      </span>
      <div className="jw__screen">
        <svg
          className={`jw__plot jw__plot--${level}`}
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          role="img"
          aria-label={`${widget.label} ${reading === undefined ? "no data" : formatNumber(reading)}${unit}, last ${samples.length} readings`}
        >
          {shape && (
            <>
              <path className="jw__area" d={shape.area} />
              <path className="jw__line" d={shape.line} />
            </>
          )}
        </svg>
      </div>
    </Frame>
  );
}
