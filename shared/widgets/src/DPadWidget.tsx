import { useState } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { activate, type WidgetProps } from "./types";

type Direction = "up" | "right" | "down" | "left";

/** Cross layout on a 3x3 grid; the center cell is a decorative dimple. */
const BUTTONS: { dir: Direction; label: string; row: number; col: number }[] = [
  { dir: "up", label: "Up", row: 1, col: 2 },
  { dir: "left", label: "Left", row: 2, col: 1 },
  { dir: "right", label: "Right", row: 2, col: 3 },
  { dir: "down", label: "Down", row: 3, col: 2 },
];

export default function DPadWidget(props: WidgetProps) {
  const { widget, mode } = props;
  const [held, setHeld] = useState<Direction | null>(null);

  return (
    <Frame props={props}>
      <WidgetTitle widget={widget} />
      <div className="jw__ringbox">
        <div className="jw__dpad" role="group" aria-label={widget.label}>
          <div className="jw__dcenter" aria-hidden="true" />
          {BUTTONS.map(({ dir, label, row, col }) => (
            <button
              key={dir}
              type="button"
              className={`jw__dbtn jw__dbtn--${dir} ${held === dir ? "jw__dbtn--held" : ""}`}
              style={{ gridRow: row, gridColumn: col }}
              aria-label={label}
              onPointerDown={(e) => {
                if (mode === "live") {
                  e.currentTarget.setPointerCapture(e.pointerId);
                  setHeld(dir);
                }
                activate(props, dir);
              }}
              onPointerUp={() => setHeld(null)}
              onPointerCancel={() => setHeld(null)}
              // Keyboard activation only (detail is 0); pointer presses are handled on pointerdown.
              onClick={(e) => e.detail === 0 && activate(props, dir)}
            >
              <svg viewBox="0 0 24 24" className="jw__arrow" aria-hidden="true">
                <path d="M6 15l6-6 6 6" />
              </svg>
            </button>
          ))}
        </div>
      </div>
    </Frame>
  );
}
