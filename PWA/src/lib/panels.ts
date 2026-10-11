import type { SavedPanel } from "@janus/schema";
import { DEMO_HOST } from "./demo";

const KEY = "janus.panels";
const DEMO_SEEDED_KEY = "janus.demoSeeded";
const DEMO_ID = "demo";

function read(): SavedPanel[] | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as SavedPanel[]) : null;
  } catch {
    return null;
  }
}

function write(panels: SavedPanel[]): SavedPanel[] {
  try {
    localStorage.setItem(KEY, JSON.stringify(panels));
  } catch {
    // storage unavailable (private mode); the change just won't persist
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
  write(listPanels().map((p) => (p.id === id ? { ...p, ...connection } : p)));

export const removePanel = (id: string): SavedPanel[] => write(listPanels().filter((p) => p.id !== id));

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
