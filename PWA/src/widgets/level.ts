import { isDialWidget } from "./types";
import type { Widget } from "@janus/schema";

/** Live data is cyan; it escalates to amber at `warn` and coral at `critical`. */
export function levelOf(widget: Widget, reading: number | undefined): "ok" | "warn" | "critical" {
  if (reading === undefined || !isDialWidget(widget)) return "ok";
  if (widget.critical !== undefined && reading >= widget.critical) return "critical";
  if (widget.warn !== undefined && reading >= widget.warn) return "warn";
  return "ok";
}
