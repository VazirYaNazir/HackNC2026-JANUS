import type { SavedConnection } from "@janus/schema";

const KEY = "janus.connection";

export function loadConnection(): SavedConnection | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as SavedConnection) : null;
  } catch {
    return null;
  }
}

export function saveConnection(conn: SavedConnection): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(conn));
  } catch {
    // storage unavailable (private mode); the session just won't persist
  }
}

export function clearConnection(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // nothing to clear
  }
}

export async function apiFetch<T>(conn: SavedConnection, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(new URL(path, conn.host), {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${conn.token}` },
  });
  if (!res.ok) throw new Error(`${init.method ?? "GET"} ${path} failed: ${res.status}`);
  return (await res.json()) as T;
}
