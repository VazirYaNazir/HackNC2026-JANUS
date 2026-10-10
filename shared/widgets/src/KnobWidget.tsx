import { useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { sweepPath } from "./arc";
import { activate, isInputWidget, type WidgetProps } from "./types";

const ARC = sweepPath(40);
/** Vertical drag distance that sweeps the whole range. */
const DRAG_RANGE_PX = 160;

export default function KnobWidget(props: WidgetProps) {
  const { widget, mode, value } = props;
  const [draft, setDraft] = useState<number | null>(null);
  const drag = useRef<{ y: number; start: number } | null>(null);

  const input = isInputWidget(widget) ? widget : null;
  const min = input?.min ?? 0;
  const max = input?.max ?? 100;
  const step = input?.step ?? 1;
  const current = draft ?? (typeof value === "number" ? value : min);
  const fraction = max > min ? Math.min(1, Math.max(0, (current - min) / (max - min))) : 0;

  const snap = (v: number) => {
    const stepped = Math.round((v - min) / step) * step + min;
    return Math.min(max, Math.max(min, Math.round(stepped * 1000) / 1000));
  };

  // Dragging only updates the draft; the action fires once on release.
  const commit = () => {
    drag.current = null;
    if (draft === null) return;
    activate(props, draft);
    setDraft(null);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (mode === "edit") {
      activate(props);
      return;
    }
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { y: e.clientY, start: current };
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (d) setDraft(snap(d.start + ((d.y - e.clientY) / DRAG_RANGE_PX) * (max - min)));
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (mode === "edit") return;
    const big = (max - min) / 10;
    const next: Record<string, number> = {
      ArrowUp: current + step,
      ArrowRight: current + step,
      ArrowDown: current - step,
      ArrowLeft: current - step,
      PageUp: current + big,
      PageDown: current - big,
      Home: min,
      End: max,
    };
    if (e.key in next) {
      e.preventDefault();
      setDraft(snap(next[e.key]));
    }
  };

  return (
    <Frame props={props}>
      <div className="jw__row">
        <WidgetTitle widget={widget} />
        <span className="jw__readout jw__readout--amber">{current}</span>
      </div>
      <div className="jw__ringbox">
        <div className="jw__ring">
          <svg viewBox="0 0 100 100" aria-hidden="true">
            <path className="jw__arc jw__arc--groove-dark" d={ARC} pathLength={100} />
            <path className="jw__arc jw__arc--groove-light" d={ARC} pathLength={100} />
            <path className="jw__arc jw__arc--track" d={ARC} pathLength={100} />
            {fraction > 0 && (
              <path className="jw__arc jw__arc--amber" d={ARC} pathLength={100} strokeDasharray={`${fraction * 100} 100`} />
            )}
          </svg>
          <div className="jw__dial" aria-hidden="true">
            <div className="jw__pointer" style={{ "--rot": `${-135 + fraction * 270}deg` } as CSSProperties} />
            <span className="jw__big">{current}</span>
          </div>
        </div>
      </div>
      {/* The whole tile is the drag surface, so even a 1x1 knob has a comfortable touch target. */}
      <div
        className="jw__surface"
        role="slider"
        tabIndex={0}
        aria-label={widget.label}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={current}
        aria-disabled={mode === "edit"}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={commit}
        onPointerCancel={commit}
        onKeyDown={onKeyDown}
        onKeyUp={commit}
        onBlur={commit}
      />
    </Frame>
  );
}
