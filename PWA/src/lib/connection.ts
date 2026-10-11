import type { PairQR, SavedConnection } from "@janus/schema";

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
  /** The PC's address: echoes the QR's host, or is resolved from a typed code. */
  host: string;
}

/**
 * Reads a pairing link, `<host>?pairing_code=<6 hex characters>`: the text the PC's QR code encodes, and what
 * you can paste instead. The scheme is optional (https is assumed). Returns null if it isn't one.
 */
export function parsePairingLink(text: string): PairQR | null {
  const raw = text.trim();
  if (!raw) return null;
  try {
    const url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    const code = (url.searchParams.get("pairing_code") ?? "").toUpperCase();
    if (!/^[0-9A-F]{6}$/.test(code)) return null;
    return { host: url.origin, code };
  } catch {
    return null;
  }
}

export const MOCK_HOST = "https://mock.janus.local";

/**
 * Pairs with a PC using its pairing code. `host` comes from a scanned QR; a typed 6-character code has no
 * host, so the real version must find the PC from the code alone. Mock for now: resolves after 500ms with a
 * fake token. The real POST /api/pair replaces the body of this one function.
 */
export async function pair(code: string, host?: string): Promise<PairResult> {
  void code;
  await new Promise((resolve) => setTimeout(resolve, 500));
  return { token: `mock-${crypto.randomUUID()}`, pcName: "my-pc", host: host ?? MOCK_HOST };
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
