import type { SavedPanel } from "@janus/schema";
import { DEMO_HOST } from "./demo";
import { clearCachedDocument } from "./layoutCache";

const KEY = "janus.panels";
const DEMO_SEEDED_KEY = "janus.demoSeeded";
const DEMO_ID = "demo";
let transient: SavedPanel[] | null = null;

function read(): SavedPanel[] | null {
  try {
    if (transient) return transient;
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!Array.isArray(parsed)) return null;
    const seen = new Set<string>();
    return parsed.filter((p): p is SavedPanel => {
      if (!p || typeof p !== 'object' || !['id', 'name', 'pcName', 'host', 'token'].every(key => typeof p[key] === 'string' && p[key].length > 0)
        || typeof p.addedAt !== 'number' || !Number.isFinite(p.addedAt) || seen.has(p.id)) return false;
      seen.add(p.id); return true;
    });
  } catch {
    return null;
  }
}

function write(panels: SavedPanel[]): SavedPanel[] {
  try {
    localStorage.setItem(KEY, JSON.stringify(panels));
    transient = null;
  } catch {
    // A just-used pairing code must retain its credential for the current session.
    transient = panels;
  }
  return panels;
}

export const listPanels = (): SavedPanel[] => read() ?? [];

export function addPanel(input: Pick<SavedPanel, "name" | "pcName" | "host" | "token" | "agentDeviceId">): SavedPanel[] {
  const panel: SavedPanel = { ...input, id: crypto.randomUUID(), addedAt: Date.now() };
  return write([...listPanels(), panel]);
}

export const renamePanel = (id: string, name: string): SavedPanel[] =>
  write(listPanels().map((p) => (p.id === id ? { ...p, name } : p)));

/** Replaces the connection of a saved panel after pairing again. Its id, name and history stay. */
export const repairPanel = (id: string, connection: Pick<SavedPanel, "host" | "token" | "pcName" | "agentDeviceId">): SavedPanel[] =>
  write(listPanels().map((p) => (p.id === id ? { ...p, host: connection.host, token: connection.token, pcName: connection.pcName, agentDeviceId: connection.agentDeviceId } : p)));

export function removePanel(id: string): SavedPanel[] {
  clearCachedDocument(id); // its saved layout goes with it
  return write(listPanels().filter((p) => p.id !== id));
}

export const touchPanel = (id: string): SavedPanel[] =>
  write(listPanels().map((p) => (p.id === id ? { ...p, lastOpenedAt: Date.now() } : p)));

export const hasDemoPanel = (): boolean => listPanels().some((p) => p.id === DEMO_ID);

/** Adds the Demo panel (simulated actions, no network) unless it is already there. */
export function addDemoPanel(): SavedPanel[] {
  if (hasDemoPanel()) return listPanels();
  const demo: SavedPanel = { id: DEMO_ID, name: "Desk Control", pcName: "Demo PC", host: DEMO_HOST, token: "demo", addedAt: Date.now() };
  return write([...listPanels(), demo]);
}

/** First launch only: adds the Demo panel, then sets a flag so it is never re-added after the user removes it. */
export function seedDemoPanel(): void {
  try {
    if (localStorage.getItem(DEMO_SEEDED_KEY)) return;
    addDemoPanel();
    localStorage.setItem(DEMO_SEEDED_KEY, "1");
  } catch {
    // storage unavailable (private mode): there is nowhere to remember it, so skip seeding
  }
}
