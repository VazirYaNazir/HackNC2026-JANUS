import { useState, type CSSProperties } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { activate, isInputWidget, type WidgetProps } from "./types";

export default function SliderWidget(props: WidgetProps) {
  const { widget, mode, value } = props;
  const [draft, setDraft] = useState<number | null>(null);
  const min = isInputWidget(widget) ? (widget.min ?? 0) : 0;
  const max = isInputWidget(widget) ? (widget.max ?? 100) : 100;
  const step = isInputWidget(widget) ? (widget.step ?? 1) : 1;
  const current = draft ?? (typeof value === "number" ? value : min);
  const pct = max > min ? ((current - min) / (max - min)) * 100 : 0;

  // Dragging only updates the draft; the action fires once on release.
  const commit = () => {
    if (draft === null) return;
    activate(props, draft);
    setDraft(null);
  };

  return (
    <Frame props={props} selectable>
      <WidgetTitle widget={widget} />
      <input
        type="range"
        className="jw__range"
        style={{ "--pct": `${pct}%` } as CSSProperties}
        aria-label={widget.label}
        min={min}
        max={max}
        step={step}
        value={current}
        disabled={mode === "edit"}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
      <span className="jw__readout jw__readout--amber">{current}</span>
    </Frame>
  );
}
