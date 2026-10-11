import { useState, type CSSProperties } from "react";
import { Frame } from "./Frame";
import WidgetTitle from "./WidgetTitle";
import { activate, isInputWidget, type WidgetProps } from "./types";
import { isNativeAction } from '@janus/schema';
import { useThrottledSend } from './useThrottledSend';

export default function SliderWidget(props: WidgetProps) {
  const { widget, mode, value } = props;
  const native = isInputWidget(widget) && isNativeAction(widget.action);
  const { push, sendNow } = useThrottledSend<number>((value) => activate(props, value), 100);
  const [draft, setDraft] = useState<number | null>(null);
  const [held, setHeld] = useState(false);
  const min = isInputWidget(widget) ? (widget.min ?? 0) : 0;
  const max = isInputWidget(widget) ? (widget.max ?? 100) : 100;
  const step = isInputWidget(widget) ? (widget.step ?? 1) : 1;
  const current = draft ?? (typeof value === "number" ? value : min);
  const pct = max > min ? ((current - min) / (max - min)) * 100 : 0;

  // Dragging only updates the draft; the action fires once on release.
  const commit = () => {
    if (draft === null) return;
    if (native) sendNow(draft);
    else activate(props, draft);
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
        onChange={(e) => { const next = Number(e.target.value); setDraft(next); if (native) push(next); }}
        data-active={held || undefined}
        onPointerDown={() => setHeld(true)}
        onPointerUp={() => {
          setHeld(false);
          commit();
        }}
        onPointerCancel={() => setHeld(false)}
        onKeyUp={commit}
        onBlur={commit}
      />
      <span className="jw__readout jw__readout--amber">{current}</span>
    </Frame>
  );
}
