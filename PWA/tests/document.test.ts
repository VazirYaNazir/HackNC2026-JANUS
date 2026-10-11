import { describe, expect, it } from "vitest";
import { latestDocument, parsePanelDocument, PanelDocumentError, type PanelDocument } from "@janus/schema";

const widget = (over: Record<string, unknown> = {}) => ({ id: "a", type: "button", label: "A", action: "x", x: 0, y: 0, w: 100, h: 50, ...over });
const v2 = (widgets: unknown[] = [widget()], layout: Record<string, unknown> = {}, revision: unknown = 3) => ({
  revision,
  layout: { schemaVersion: 2, coordinateSpace: "logical", canvas: { width: 360, height: 780 }, name: "Panel", widgets, ...layout },
});
const v1 = (layout: Record<string, unknown> = {}) => ({
  revision: 1,
  layout: { schemaVersion: 1, name: "Old", columns: 4, widgets: [widget({ w: 2, h: 1 })], ...layout },
});

describe("parsePanelDocument", () => {
  it("accepts a version 2 document and returns it unchanged", () => {
    const doc = v2();
    expect(parsePanelDocument(doc)).toBe(doc);
  });

  it("accepts a version 1 document", () => {
    expect(parsePanelDocument(v1()).revision).toBe(1);
  });

  it("accepts an empty widget list", () => {
    expect(() => parsePanelDocument(v2([]))).not.toThrow();
  });

  it("accepts revision 0, which is what an empty panel reports", () => {
    expect(() => parsePanelDocument(v2([], {}, 0))).not.toThrow();
  });

  it("accepts widget types it does not know, so the phone can show them as Unsupported", () => {
    expect(() => parsePanelDocument(v2([widget({ type: "hologram", action: undefined })]))).not.toThrow();
  });

  it("accepts every optional field when it has the right type", () => {
    const rich = widget({ icon: "save", color: "amber", confirm: true, variant: "push", type: "slider", min: 0, max: 10, step: 1, unit: "%" });
    expect(() => parsePanelDocument(v2([rich]))).not.toThrow();
  });

  it.each<[string, unknown, string]>([
    ["a string", "nope", "the document must be an object"],
    ["null", null, "the document must be an object"],
    ["an array", [], "the document must be an object"],
    ["a negative revision", v2([widget()], {}, -1), "revision must be a whole number"],
    ["a fractional revision", v2([widget()], {}, 1.5), "revision must be a whole number"],
    ["a text revision", v2([widget()], {}, "3"), "revision must be a whole number"],
    ["no layout", { revision: 1 }, "layout must be an object"],
    ["a layout that is a list", { revision: 1, layout: [] }, "layout must be an object"],
    ["a layout without a name", v2([widget()], { name: undefined }), "layout.name must be a string"],
    ["a version 2 layout in the wrong space", v2([widget()], { coordinateSpace: "grid" }), 'layout.coordinateSpace must be "logical"'],
    ["a canvas with no height", v2([widget()], { canvas: { width: 360, height: 0 } }), "layout.canvas must be a width and height above 0"],
    ["a canvas that is not an object", v2([widget()], { canvas: 5 }), "layout.canvas"],
    ["a version 1 layout with no columns", v1({ columns: 0 }), "layout.columns must be a whole number above 0"],
    ["widgets that are not a list", v2([], { widgets: {} }), "layout.widgets must be a list"],
    ["a widget that is not an object", v2([5]), "layout.widgets[0] must be an object"],
    ["a widget without an id", v2([widget({ id: "" })]), "layout.widgets[0].id must be a non-empty string"],
    ["a widget without a type", v2([widget({ type: undefined })]), "layout.widgets[0].type must be a string"],
    ["a widget without a label", v2([widget({ label: undefined })]), "layout.widgets[0].label must be a string"],
    ["a position given as text", v2([widget({ x: "5" })]), "layout.widgets[0].x must be a finite number"],
    ["a size that is infinite", v2([widget({ w: Infinity })]), "layout.widgets[0].w must be a finite number"],
    ["an input without an action", v2([widget({ action: undefined })]), "layout.widgets[0].action must be a string"],
    ["a dial without a source", v2([widget({ type: "gauge", action: undefined })]), "layout.widgets[0].source must be a string"],
    ["an unknown variant", v2([widget({ variant: "wobble" })]), 'layout.widgets[0].variant must be "switch" or "push"'],
    ["a color outside the palette", v2([widget({ color: "pink" })]), "layout.widgets[0].color"],
    ["a minimum given as text", v2([widget({ type: "slider", min: "0" })]), "layout.widgets[0].min must be a finite number"],
    ["an icon that is not text", v2([widget({ icon: 7 })]), "layout.widgets[0].icon must be a string"],
  ])("rejects %s", (_name, doc, message) => {
    expect(() => parsePanelDocument(doc)).toThrow(PanelDocumentError);
    expect(() => parsePanelDocument(doc)).toThrow(message);
  });

  it("names the position of the widget that is wrong", () => {
    const doc = v2([widget({ id: "a" }), widget({ id: "b", x: 100 }), widget({ id: "c", h: "tall" })]);
    expect(() => parsePanelDocument(doc)).toThrow("layout.widgets[2].h");
  });

  it("tells the user to update the app for a layout version it does not know", () => {
    const doc = { revision: 1, layout: { schemaVersion: 3, name: "Future", widgets: [] } };
    expect(() => parsePanelDocument(doc)).toThrow("version 3");
    expect(() => parsePanelDocument(doc)).toThrow("Update JANUS");
  });

  it("always starts its message in a way that is safe to show to the user", () => {
    try {
      parsePanelDocument({ revision: 1 });
      expect.unreachable();
    } catch (error) {
      expect((error as Error).message).toMatch(/^The saved panel is invalid: /);
    }
  });
});

describe("latestDocument", () => {
  const at = (revision: number, name = `r${revision}`): PanelDocument => ({
    revision,
    layout: { schemaVersion: 2, coordinateSpace: "logical", canvas: { width: 360, height: 780 }, name, widgets: [] },
  });

  it("takes the first document when there is none yet", () => {
    const first = at(2);
    expect(latestDocument(null, first)).toBe(first);
  });

  it("takes a newer revision", () => {
    const newer = at(6);
    expect(latestDocument(at(5), newer)).toBe(newer);
  });

  it("ignores an older revision that arrives late", () => {
    const current = at(5);
    expect(latestDocument(current, at(4))).toBe(current);
  });

  it("accepts the same revision again", () => {
    const again = at(5, "again");
    expect(latestDocument(at(5), again)).toBe(again);
  });

  it("settles on the newest document whatever order they arrive in", () => {
    const arrivals = [at(3), at(7), at(5), at(7, "dup"), at(2), at(6)];
    expect(arrivals.reduce<PanelDocument | null>((shown, doc) => latestDocument(shown, doc), null)?.revision).toBe(7);
    const reversed = [...arrivals].reverse();
    expect(reversed.reduce<PanelDocument | null>((shown, doc) => latestDocument(shown, doc), null)?.revision).toBe(7);
  });
});
