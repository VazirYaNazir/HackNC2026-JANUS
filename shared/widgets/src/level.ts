import { isDialWidget } from "./types";
import type { Widget } from "@janus/schema";

interface Thresholds { warn?: number; critical?: number }

/** Live data is cyan; it escalates to amber at `warn` and coral at `critical`. */
export function levelOf(widget: Widget, reading: number | undefined, defaults: Thresholds = {}): "ok" | "warn" | "critical" {
  if (reading === undefined || !isDialWidget(widget)) return "ok";
  const warn = widget.warn ?? defaults.warn;
  const critical = widget.critical ?? defaults.critical;
  if (critical !== undefined && reading >= critical) return "critical";
  if (warn !== undefined && reading >= warn) return "warn";
  return "ok";
}
