import { describe, expect, it } from "vitest";
import type { PanelDocument } from "@janus/schema";
import { clearCachedDocument, loadCachedDocument, saveCachedDocument, type CacheStorage } from "../src/lib/layoutCache";

function memoryStorage(): CacheStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
  };
}

const widget = (over: Record<string, unknown> = {}) => ({ id: "a", type: "button", label: "A", action: "x", x: 0, y: 0, w: 100, h: 50, ...over });
const doc = (revision = 4, widgets: unknown[] = [widget()]): PanelDocument =>
  ({
    revision,
    layout: { schemaVersion: 2, coordinateSpace: "logical", canvas: { width: 360, height: 780 }, name: "Panel", widgets },
  }) as PanelDocument;

describe("layout cache", () => {
  it("returns nothing for a panel that was never saved", () => {
    expect(loadCachedDocument("p1", memoryStorage())).toBeNull();
  });

  it("gives back what was saved", () => {
    const storage = memoryStorage();
    saveCachedDocument("p1", doc(9), storage);
    expect(loadCachedDocument("p1", storage)).toEqual(doc(9));
  });

  it("replaces the previous layout when saved again", () => {
    const storage = memoryStorage();
    saveCachedDocument("p1", doc(1), storage);
    saveCachedDocument("p1", doc(2), storage);
    expect(loadCachedDocument("p1", storage)?.revision).toBe(2);
    expect(storage.data.size).toBe(1);
  });

  it("keeps each panel's layout separate", () => {
    const storage = memoryStorage();
    saveCachedDocument("p1", doc(1), storage);
    saveCachedDocument("p2", doc(2), storage);
    expect(loadCachedDocument("p1", storage)?.revision).toBe(1);
    expect(loadCachedDocument("p2", storage)?.revision).toBe(2);
  });

  it("clears one panel without touching another", () => {
    const storage = memoryStorage();
    saveCachedDocument("p1", doc(1), storage);
    saveCachedDocument("p2", doc(2), storage);
    clearCachedDocument("p1", storage);
    expect(loadCachedDocument("p1", storage)).toBeNull();
    expect(loadCachedDocument("p2", storage)?.revision).toBe(2);
  });

  describe("an entry that cannot be trusted is ignored and deleted", () => {
    const stored = (value: string) => {
      const storage = memoryStorage();
      storage.setItem("janus.layout.p1", value);
      return storage;
    };

    it.each<[string, string]>([
      ["text that is not JSON", "{oops"],
      ["JSON with no document", JSON.stringify({ savedAt: 1 })],
      ["a document with the wrong shape", JSON.stringify({ document: { revision: 1, layout: { schemaVersion: 2 } } })],
      ["a negative revision", JSON.stringify({ document: { ...doc(), revision: -3 } })],
      ["a layout version this app does not know", JSON.stringify({ document: { revision: 1, layout: { schemaVersion: 9, name: "x", widgets: [] } } })],
      ["widgets that overlap", JSON.stringify({ document: doc(1, [widget({ id: "a" }), widget({ id: "b", x: 50 })]) })],
      ["a widget outside the canvas", JSON.stringify({ document: doc(1, [widget({ x: 340 })]) })],
      ["two widgets with one id", JSON.stringify({ document: doc(1, [widget(), widget({ x: 200 })]) })],
    ])("%s", (_name, value) => {
      const storage = stored(value);
      expect(loadCachedDocument("p1", storage)).toBeNull();
      expect(storage.data.has("janus.layout.p1")).toBe(false);
    });
  });

  describe("when storage fails", () => {
    const broken: CacheStorage = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("quota exceeded");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    };

    it("saving does not throw", () => {
      expect(() => saveCachedDocument("p1", doc(), broken)).not.toThrow();
    });

    it("loading reports nothing instead of throwing", () => {
      expect(loadCachedDocument("p1", broken)).toBeNull();
    });

    it("clearing does not throw", () => {
      expect(() => clearCachedDocument("p1", broken)).not.toThrow();
    });
  });
});
