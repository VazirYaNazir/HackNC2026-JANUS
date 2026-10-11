import { normalizeLayout } from "@janus/design";
import { parsePanelDocument, type PanelDocument } from "@janus/schema";

/** Where the cache lives. Defaults to localStorage; tests pass an in-memory one. */
export type CacheStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const key = (panelId: string) => `janus.layout.${panelId}`;

/** Remembers the last valid layout of a panel, so it can be shown when the PC is unreachable. */
export function saveCachedDocument(panelId: string, document: PanelDocument, storage: CacheStorage = localStorage): void {
  try {
    storage.setItem(key(panelId), JSON.stringify({ document, savedAt: Date.now() }));
  } catch {
    // storage full or unavailable: the panel just won't be available offline
  }
}

/**
 * The cached layout, or null. What is stored is not trusted: it is checked like a document from the
 * server, and an entry that no longer passes (damaged, or from an older app version) is deleted.
 */
export function loadCachedDocument(panelId: string, storage: CacheStorage = localStorage): PanelDocument | null {
  try {
    const raw = storage.getItem(key(panelId));
    if (!raw) return null;
    try {
      const document = parsePanelDocument((JSON.parse(raw) as { document?: unknown }).document);
      normalizeLayout(document.layout);
      return document;
    } catch {
      storage.removeItem(key(panelId));
      return null;
    }
  } catch {
    return null;
  }
}

export function clearCachedDocument(panelId: string, storage: CacheStorage = localStorage): void {
  try {
    storage.removeItem(key(panelId));
  } catch {
    // nothing to clear
  }
}
