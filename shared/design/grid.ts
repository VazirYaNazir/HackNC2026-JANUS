import type { Layout, LegacyGridLayout, LogicalCanvasLayout, Widget } from "@janus/schema";

export const GRID = { columns: 4, rowHeight: 62, gap: 12, padding: 18 } as const;
export const LOGICAL_CANVAS = { width: 360, height: 780 } as const;

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type GeometryErrorCode =
  | "unsupported-version"
  | "invalid-layout"
  | "invalid-geometry"
  | "out-of-bounds"
  | "collision";

export class LayoutGeometryError extends Error {
  readonly code: GeometryErrorCode;
  readonly widgetId?: string;
  readonly geometry?: Rect;

  constructor(code: GeometryErrorCode, message: string, widgetId?: string, geometry?: Rect) {
    super(message);
    this.name = "LayoutGeometryError";
    this.code = code;
    this.widgetId = widgetId;
    this.geometry = geometry;
  }
}

export interface PlacementValidation {
  valid: boolean;
  reason?: "invalid-geometry" | "out-of-bounds" | "collision";
  collidingId?: string;
}

const geometryText = ({ x, y, w, h }: Rect) => `x=${x}, y=${y}, w=${w}, h=${h}`;
const isFiniteRect = (rect: Rect) => [rect.x, rect.y, rect.w, rect.h].every(Number.isFinite);

export function widgetRect(widget: Widget): Rect {
  return { x: widget.x, y: widget.y, w: widget.w, h: widget.h };
}

export function gridCellWidth(columns: number = GRID.columns): number {
  return (LOGICAL_CANVAS.width - (columns - 1) * GRID.gap) / columns;
}

export function gridRectToLogical(rect: Rect, columns: number = GRID.columns): Rect {
  const cellWidth = gridCellWidth(columns);
  return {
    x: rect.x * (cellWidth + GRID.gap),
    y: rect.y * (GRID.rowHeight + GRID.gap),
    w: rect.w * cellWidth + (rect.w - 1) * GRID.gap,
    h: rect.h * GRID.rowHeight + (rect.h - 1) * GRID.gap,
  };
}

export function gridSizeToLogical(w: number, h: number, columns: number = GRID.columns): Pick<Rect, "w" | "h"> {
  const converted = gridRectToLogical({ x: 0, y: 0, w, h }, columns);
  return { w: converted.w, h: converted.h };
}

export function snapPositionToGrid(rect: Rect, columns: number = GRID.columns): Rect {
  const columnStride = gridCellWidth(columns) + GRID.gap;
  const rowStride = GRID.rowHeight + GRID.gap;
  return {
    ...rect,
    x: Math.round(rect.x / columnStride) * columnStride,
    y: Math.round(rect.y / rowStride) * rowStride,
  };
}

export function rectsOverlap(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

export function validatePlacement(
  rect: Rect,
  widgets: readonly Widget[],
  ignoreId?: string,
  canvas: { width: number; height: number } = LOGICAL_CANVAS,
): PlacementValidation {
  if (!isFiniteRect(rect) || rect.w <= 0 || rect.h <= 0) return { valid: false, reason: "invalid-geometry" };
  if (rect.x < 0 || rect.y < 0 || rect.x + rect.w > canvas.width || rect.y + rect.h > canvas.height) {
    return { valid: false, reason: "out-of-bounds" };
  }
  const collision = widgets.find((widget) => widget.id !== ignoreId && rectsOverlap(rect, widgetRect(widget)));
  return collision ? { valid: false, reason: "collision", collidingId: collision.id } : { valid: true };
}

function validateWidgetGeometry(
  widget: Widget,
  widgets: readonly Widget[],
  canvas: { width: number; height: number } = LOGICAL_CANVAS,
  legacy = false,
): void {
  const rect = widgetRect(widget);
  const result = validatePlacement(rect, widgets, widget.id, canvas);
  if (result.valid) return;

  const prefix = legacy ? "Legacy widget" : "Widget";
  const suffix = result.reason === "collision"
    ? `overlaps widget "${result.collidingId}"`
    : result.reason === "out-of-bounds"
      ? `extends beyond the ${canvas.width} × ${canvas.height} canvas`
      : "contains non-finite or non-positive values";
  throw new LayoutGeometryError(
    result.reason ?? "invalid-geometry",
    `${prefix} "${widget.id}" has invalid geometry ${geometryText(rect)}: ${suffix}.`,
    widget.id,
    rect,
  );
}

function validateIds(widgets: readonly Widget[]): void {
  const seen = new Set<string>();
  for (const widget of widgets) {
    if (!widget.id || seen.has(widget.id)) {
      throw new LayoutGeometryError("invalid-layout", `Layout contains a missing or duplicate widget ID: "${widget.id}".`, widget.id);
    }
    seen.add(widget.id);
  }
}

export function validateLogicalLayout(layout: LogicalCanvasLayout): void {
  if (
    layout.coordinateSpace !== "logical" ||
    layout.canvas.width !== LOGICAL_CANVAS.width ||
    layout.canvas.height !== LOGICAL_CANVAS.height
  ) {
    throw new LayoutGeometryError(
      "invalid-layout",
      `Version 2 layouts must use the ${LOGICAL_CANVAS.width} × ${LOGICAL_CANVAS.height} logical canvas.`,
    );
  }
  validateIds(layout.widgets);
  for (const widget of layout.widgets) validateWidgetGeometry(widget, layout.widgets, layout.canvas);
}

function migrateLegacyLayout(layout: LegacyGridLayout): LogicalCanvasLayout {
  if (!Number.isInteger(layout.columns) || layout.columns <= 0) {
    throw new LayoutGeometryError("invalid-layout", `Legacy layout has invalid column count: ${layout.columns}.`);
  }
  validateIds(layout.widgets);

  const widgets = layout.widgets.map((widget) => {
    const legacyRect = widgetRect(widget);
    if (!isFiniteRect(legacyRect) || ![legacyRect.x, legacyRect.y, legacyRect.w, legacyRect.h].every(Number.isInteger)) {
      throw new LayoutGeometryError(
        "invalid-geometry",
        `Legacy widget "${widget.id}" has invalid geometry ${geometryText(legacyRect)}: grid values must be finite integers.`,
        widget.id,
        legacyRect,
      );
    }
    const rect = gridRectToLogical(legacyRect, layout.columns);
    const migrated = { ...widget, ...rect };
    validateWidgetGeometry(migrated, [], LOGICAL_CANVAS, true);
    return migrated;
  });

  const normalized: LogicalCanvasLayout = {
    schemaVersion: 2,
    coordinateSpace: "logical",
    canvas: { ...LOGICAL_CANVAS },
    name: layout.name,
    widgets,
  };
  validateLogicalLayout(normalized);
  return normalized;
}

/** Converts legacy grid layouts exactly once. Valid version 2 layouts are returned by identity. */
export function normalizeLayout(layout: Layout): LogicalCanvasLayout {
  if (layout.schemaVersion === 2) {
    validateLogicalLayout(layout);
    return layout;
  }
  if (layout.schemaVersion === 1) return migrateLegacyLayout(layout);
  const version = (layout as { schemaVersion?: unknown }).schemaVersion;
  throw new LayoutGeometryError("unsupported-version", `Unsupported layout schema version: ${String(version)}.`);
}

/** The lowest edge of any rect (y + h), in logical units. 0 when there are none. */
export function contentBottom(rects: readonly Rect[]): number {
  return rects.reduce((bottom, rect) => Math.max(bottom, rect.y + rect.h), 0);
}

export function logicalRectStyle(
  rect: Rect,
  canvas: { width: number; height: number } = LOGICAL_CANVAS,
): Record<"left" | "top" | "width" | "height", string> {
  return {
    left: `${(rect.x / canvas.width) * 100}%`,
    top: `${(rect.y / canvas.height) * 100}%`,
    width: `${(rect.w / canvas.width) * 100}%`,
    height: `${(rect.h / canvas.height) * 100}%`,
  };
}
