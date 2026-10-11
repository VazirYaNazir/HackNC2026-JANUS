import { useState } from "react";
import { GRID, Icon } from "@janus/design";
import type { Layout, SavedPanel, Widget } from "@janus/schema";
import { PanelGrid, isInputWidget, type WidgetValue } from "@janus/widgets";
import "./PanelScreen.css";

interface PanelScreenProps {
  panel: SavedPanel;
  layout: Layout;
  onBack: () => void;
  /** Live readings keyed by DialWidget.source. */
  data?: Record<string, number | string>;
  /** Starting values for stateful inputs, keyed by widget id. */
  initialValues?: Record<string, WidgetValue>;
}

export default function PanelScreen({ panel, layout, onBack, data = {}, initialValues = {} }: PanelScreenProps) {
  const [values, setValues] = useState<Record<string, WidgetValue>>(initialValues);

  const handleTrigger = (widget: Widget, value?: WidgetValue) => {
    if (!isInputWidget(widget)) return;
    console.log("[janus] trigger", { panel: panel.id, widget: widget.id, action: widget.action, value });
    if (value !== undefined) setValues((prev) => ({ ...prev, [widget.id]: value }));
  };

  return (
    <main className="panel" style={{ padding: `${GRID.padding}px` }}>
      <header className="panel__header">
        <button type="button" className="panel__back" aria-label="Back to panels" onClick={onBack}>
          <Icon name="back" size={24} />
        </button>
        <h1 className="panel__title">{panel.name}</h1>
      </header>
      <PanelGrid layout={layout} values={values} data={data} onTrigger={handleTrigger} />
    </main>
  );
}
