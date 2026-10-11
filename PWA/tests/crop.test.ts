import { describe, expect, it } from "vitest";
import { contentBottom, gridRectToLogical, logicalRectStyle, LOGICAL_CANVAS, normalizeLayout } from "@janus/design";
import type { LogicalCanvasLayout, Widget } from "@janus/schema";

const rect = (x: number, y: number, w: number, h: number) => ({ x, y, w, h });

describe("contentBottom", () => {
  it("is 0 when there is nothing", () => {
    expect(contentBottom([])).toBe(0);
  });

  it("is the bottom edge of a single widget", () => {
    expect(contentBottom([rect(10, 300, 100, 60)])).toBe(360);
  });

  it("is the lowest edge, not the lowest top and not the tallest widget", () => {
    // The tall widget starts at the top; the short one reaches lower down the screen.
    const widgets = [rect(0, 0, 100, 400), rect(120, 350, 100, 50), rect(240, 10, 100, 20)];
    expect(contentBottom(widgets)).toBe(400);
    expect(contentBottom([rect(0, 0, 100, 300), rect(120, 350, 100, 80)])).toBe(430);
  });

  it("does not depend on the order of the widgets", () => {
    const widgets = [rect(0, 0, 50, 50), rect(0, 200, 50, 80), rect(0, 100, 50, 10)];
    expect(contentBottom(widgets)).toBe(contentBottom([...widgets].reverse()));
    expect(contentBottom(widgets)).toBe(280);
  });

  it("ignores empty space above and beside widgets: only the bottom matters", () => {
    expect(contentBottom([rect(300, 500, 40, 40)])).toBe(540);
  });

  it("reaches the canvas height when a widget touches the bottom", () => {
    expect(contentBottom([rect(0, 700, 100, LOGICAL_CANVAS.height - 700)])).toBe(LOGICAL_CANVAS.height);
  });

  it("works for the demo-style layout built from grid cells", () => {
    // Two cells wide and two rows tall, starting on row 3 (0-based).
    const ask = gridRectToLogical({ x: 0, y: 3, w: 4, h: 2 });
    expect(contentBottom([ask])).toBe(5 * 62 + 4 * 12);
  });
});

describe("positions in a trimmed box", () => {
  const widget = (id: string, r: ReturnType<typeof rect>) => ({ id, type: "button", label: id, action: "x", ...r }) as Widget;
  const layout = (widgets: Widget[]): LogicalCanvasLayout => ({
    schemaVersion: 2,
    coordinateSpace: "logical",
    canvas: { ...LOGICAL_CANVAS },
    name: "T",
    widgets,
  });

  it("puts the lowest widget flush with the bottom of the trimmed box", () => {
    const widgets = [widget("a", rect(0, 0, 170, 60)), widget("b", rect(0, 200, 170, 100))];
    const trimmed = { width: LOGICAL_CANVAS.width, height: contentBottom(widgets) };
    const style = logicalRectStyle(widgets[1], trimmed);
    expect(parseFloat(style.top) + parseFloat(style.height)).toBeCloseTo(100, 6);
  });

  it("keeps the width scale of the full canvas, so widgets are as wide as in the builder", () => {
    const w = widget("a", rect(90, 20, 180, 60));
    const full = logicalRectStyle(w);
    const trimmed = logicalRectStyle(w, { width: LOGICAL_CANVAS.width, height: 80 });
    expect(trimmed.left).toBe(full.left);
    expect(trimmed.width).toBe(full.width);
  });

  it("keeps each widget's height relative to the box, so a trimmed box shows the same pixels as the canvas", () => {
    // Percent of a shorter box times the shorter box height equals percent of the canvas times the canvas height.
    const w = widget("a", rect(0, 150, 100, 90));
    const trimmedHeight = contentBottom([w]);
    const px = (style: ReturnType<typeof logicalRectStyle>, boxHeight: number) => [parseFloat(style.top) * boxHeight / 100, parseFloat(style.height) * boxHeight / 100];
    const [fullTop, fullHeight] = px(logicalRectStyle(w), LOGICAL_CANVAS.height);
    const [trimTop, trimHeight] = px(logicalRectStyle(w, { width: LOGICAL_CANVAS.width, height: trimmedHeight }), trimmedHeight);
    expect(trimTop).toBeCloseTo(fullTop, 6);
    expect(trimHeight).toBeCloseTo(fullHeight, 6);
  });

  it("measures the same bottom edge for a version 1 layout once it is normalized", () => {
    const legacy = {
      schemaVersion: 1 as const,
      name: "Old",
      columns: 4,
      widgets: [widget("a", rect(0, 0, 2, 1)), widget("b", rect(2, 3, 2, 2))],
    };
    const normalized = normalizeLayout(legacy);
    expect(contentBottom(normalized.widgets)).toBe(gridRectToLogical({ x: 2, y: 3, w: 2, h: 2 }).y + gridRectToLogical({ x: 2, y: 3, w: 2, h: 2 }).h);
    expect(normalizeLayout(layout([widget("a", rect(0, 0, 100, 50))])).widgets).toHaveLength(1);
  });
});
