import { GRID } from "@janus/design";
import { INPUT_TYPES, LEGACY_SCHEMA_VERSION, type InputType, type Layout } from "@janus/schema";

/** Shape of GET /api/v1/panels/widgets. Backend field names stay in this file. */
export interface BackendWidgetsResponse {
  widgets: { script_id: string; name: string; widget: { widget_type: string; label: string } }[];
}

const isInputType = (t: string): t is InputType => (INPUT_TYPES as readonly string[]).includes(t);

/** The backend has no positions yet, so widgets flow two per row (2x2 each). */
export function toLayout(res: BackendWidgetsResponse, name = "Panel"): Layout {
  return {
    schemaVersion: LEGACY_SCHEMA_VERSION,
    name,
    columns: GRID.columns,
    widgets: res.widgets.map((item, i) => ({
      id: item.script_id,
      type: isInputType(item.widget.widget_type) ? item.widget.widget_type : "button",
      label: item.widget.label || item.name,
      action: item.script_id,
      x: (i % 2) * 2,
      y: Math.floor(i / 2) * 2,
      w: 2,
      h: 2,
    })),
  };
}
