import { useState } from "react";
import { GRID } from "@janus/design";
import type { Layout, Widget } from "@janus/schema";
import { PanelGrid, isInputWidget, type WidgetValue } from "@janus/widgets";
import "./PanelScreen.css";

interface PanelScreenProps {
  layout: Layout;
  /** Live readings keyed by DialWidget.source. */
  data?: Record<string, number | string>;
  /** Starting values for stateful inputs, keyed by widget id. */
  initialValues?: Record<string, WidgetValue>;
}

export default function PanelScreen({ layout, data = {}, initialValues = {} }: PanelScreenProps) {
  const [values, setValues] = useState<Record<string, WidgetValue>>(initialValues);

  const handleTrigger = (widget: Widget, value?: WidgetValue) => {
    if (!isInputWidget(widget)) return;
    console.log("[janus] trigger", { widget: widget.id, action: widget.action, value });
    if (value !== undefined) setValues((prev) => ({ ...prev, [widget.id]: value }));
  };

  return (
    <main className="panel" style={{ padding: `${GRID.padding}px` }}>
      <h1 className="panel__title">{layout.name}</h1>
      <PanelGrid layout={layout} values={values} data={data} onTrigger={handleTrigger} />
    </main>
  );
}
