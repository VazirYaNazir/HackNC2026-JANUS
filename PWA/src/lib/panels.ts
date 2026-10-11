import type { SavedPanel } from "@janus/schema";

const KEY = "janus.panels";

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

export const removePanel = (id: string): SavedPanel[] => write(listPanels().filter((p) => p.id !== id));

export const touchPanel = (id: string): SavedPanel[] =>
  write(listPanels().map((p) => (p.id === id ? { ...p, lastOpenedAt: Date.now() } : p)));

/** Dev only: puts one mock panel in an empty store, once. Removing it afterwards sticks. */
export function seedMockPanel(): void {
  if (!import.meta.env.DEV || read() !== null) return;
  write([
    {
      id: "mock-desk",
      name: "Desk Control",
      pcName: "my-pc",
      host: "https://mock.janus.local",
      token: "mock-token",
      addedAt: Date.now(),
    },
  ]);
}
