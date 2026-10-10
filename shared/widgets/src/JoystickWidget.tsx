import { useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { activate, type Vec2, type WidgetProps } from "./types";
import { useThrottledSend } from "./useThrottledSend";

const SEND_INTERVAL_MS = 100;
/** Knob travel as a fraction of the base width. Matches the knob size in widgets.css (44% wide, centered). */
const TRAVEL = 0.28;
const CENTER: Vec2 = { x: 0, y: 0 };
const KEY_VECTORS: Record<string, Vec2> = {
  ArrowUp: { x: 0, y: 1 },
  ArrowDown: { x: 0, y: -1 },
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 },
};
const round2 = (n: number) => Math.round(n * 100) / 100;

export default function JoystickWidget(props: WidgetProps) {
  const { widget, mode } = props;
  const [pos, setPos] = useState<Vec2>(CENTER);
  const [active, setActive] = useState(false);
  const { push, sendNow } = useThrottledSend<Vec2>((v) => activate(props, v), SEND_INTERVAL_MS);

  const moveTo = (v: Vec2) => {
    setPos(v);
    push(v);
  };

  /** Pointer position relative to the base center, scaled to -1..1 (y up) and clamped to the unit circle. */
  const fromPointer = (e: PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const travel = rect.width * TRAVEL;
    let x = (e.clientX - (rect.left + rect.width / 2)) / travel;
    let y = -(e.clientY - (rect.top + rect.height / 2)) / travel;
    const length = Math.hypot(x, y);
    if (length > 1) {
      x /= length;
      y /= length;
    }
    return { x: round2(x), y: round2(y) };
  };

  // Springs back to center and always sends a final { x: 0, y: 0 }.
  const release = () => {
    setActive(false);
    setPos(CENTER);
    sendNow(CENTER);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (mode === "edit") {
      activate(props);
      return;
    }
    e.currentTarget.setPointerCapture(e.pointerId);
    setActive(true);
    moveTo(fromPointer(e));
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const v = KEY_VECTORS[e.key];
    if (mode === "edit" || !v) return;
    e.preventDefault();
    if (!e.repeat) setActive(true);
    moveTo(v);
  };

  const onKeyUp = (e: KeyboardEvent<HTMLDivElement>) => {
    if (mode !== "edit" && KEY_VECTORS[e.key]) release();
  };

  return (
    <Frame props={props}>
      <WidgetTitle widget={widget} />
      <div className="jw__stickbox">
        <div
          className={`jw__stick ${active ? "jw__stick--active" : ""}`}
          role="group"
          aria-label={`${widget.label} joystick, use the arrow keys`}
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={(e) => active && moveTo(fromPointer(e))}
          onPointerUp={() => active && release()}
          onPointerCancel={() => active && release()}
          onKeyDown={onKeyDown}
          onKeyUp={onKeyUp}
          onBlur={() => active && release()}
        >
          <div className="jw__stickknob" style={{ "--kx": pos.x, "--ky": pos.y } as CSSProperties} />
        </div>
      </div>
    </Frame>
  );
}
