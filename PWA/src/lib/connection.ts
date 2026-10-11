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

/** What the PC returns after a successful pairing. */
export interface PairResult {
  token: string;
  pcName: string;
}

/**
 * Pairs with a PC using the code from its QR. Mock for now: resolves after 500ms with a fake token.
 * The real POST /api/pair replaces the body of this one function.
 */
export async function pair(host: string, code: string): Promise<PairResult> {
  void host;
  void code;
  await new Promise((resolve) => setTimeout(resolve, 500));
  return { token: `mock-${crypto.randomUUID()}`, pcName: "my-pc" };
}

/** True if the host answers at all within `timeoutMs` (opaque response is enough; no CORS needed). */
export async function checkHost(host: string, timeoutMs = 3000): Promise<boolean> {
  try {
    await fetch(host, { mode: "no-cors", signal: AbortSignal.timeout(timeoutMs) });
    return true;
  } catch {
    return false;
  }
}
