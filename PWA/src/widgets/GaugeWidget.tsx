import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { isDialWidget, type WidgetProps } from "./types";
import { levelOf } from "./level";

/* 270° gauge on a 100x86 viewBox (cropped below the open end of the arc). f runs 0..1 from bottom-left, clockwise. */
const C = 50;
const R = 40;
const point = (r: number, f: number) => {
  const a = ((225 - f * 270) * Math.PI) / 180;
  return { x: C + r * Math.cos(a), y: C - r * Math.sin(a) };
};
const start = point(R, 0);
const end = point(R, 1);
const ARC = `M ${start.x} ${start.y} A ${R} ${R} 0 1 1 ${end.x} ${end.y}`;
const TICKS = Array.from({ length: 11 }, (_, i) => ({ a: point(R + 6.5, i / 10), b: point(R + 9, i / 10) }));

export default function GaugeWidget(props: WidgetProps) {
  const { widget, value } = props;
  const reading = typeof value === "number" ? value : undefined;
  const pct = reading === undefined ? 0 : Math.min(100, Math.max(0, reading));
  const unit = isDialWidget(widget) ? (widget.unit ?? "") : "";
  const level = levelOf(widget, reading, { warn: 70, critical: 90 });

  return (
    <Frame props={props} selectable>
      <WidgetTitle widget={widget} />
      <div className="jw__gauge">
        <div className="jw__face">
          <svg viewBox="0 0 100 86" role="img" aria-label={`${widget.label} ${reading ?? "no data"}${unit}`}>
            {TICKS.map((t, i) => (
              <line key={i} className="jw__tick" x1={t.a.x} y1={t.a.y} x2={t.b.x} y2={t.b.y} />
            ))}
            <path className="jw__arc jw__arc--groove-dark" d={ARC} pathLength={100} />
            <path className="jw__arc jw__arc--groove-light" d={ARC} pathLength={100} />
            <path className="jw__arc jw__arc--track" d={ARC} pathLength={100} />
            <path
              className={`jw__arc jw__arc--fill jw__arc--${level}`}
              d={ARC}
              pathLength={100}
              strokeDasharray={`${pct} 100`}
            />
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
