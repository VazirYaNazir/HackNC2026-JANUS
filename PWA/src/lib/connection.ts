import type { PairQR, SavedConnection } from "@janus/schema";
import { pairBackend, PanelClient } from "@janus/client";
import { isDemoHost } from "./demo";

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
  agentDeviceId: string;
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

/**
 * Exchange a single-use laptop-bound code for a scoped phone credential.
 */
export async function pair(host: string, code: string): Promise<PairResult> {
  return pairBackend(host, code);
}

/** True when this phone's authenticated laptop session is online. */
export async function checkHost(host: string, token: string, timeoutMs = 3000): Promise<boolean> {
  if (isDemoHost(host)) return true; // the Demo panel is always "online" and never touches the network
  try {
    return (await new PanelClient({ host, token }).session(AbortSignal.timeout(timeoutMs))).online;
  } catch {
    return false;
  }
}
