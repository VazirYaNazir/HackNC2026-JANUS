import type { PairQR } from '@janus/schema';
import { backendHost } from './api';

/** Scoped Studio codes have 12 hex characters; older pasted links used six. */
export function pairingTarget(host: string, code: string): PairQR {
  const normalized = code.trim().toUpperCase();
  if (!/^(?:[0-9A-F]{6}|[0-9A-F]{12})$/.test(normalized)) {
    throw new Error('Enter the complete pairing code shown in Studio.');
  }
  const address = host.trim();
  return { host: backendHost(/^[a-z][a-z0-9+.-]*:\/\//i.test(address) ? address : `https://${address}`), code: normalized };
}

/** Read Studio JSON QR codes, backend pairing links, or links that open the PWA. */
export function parsePairingLink(text: string): PairQR | null {
  const raw = text.trim();
  if (!raw) return null;
  try {
    if (raw.startsWith('{')) {
      const data: unknown = JSON.parse(raw);
      if (!data || typeof data !== 'object' || !('host' in data) || !('code' in data)
        || typeof data.host !== 'string' || typeof data.code !== 'string') return null;
      return pairingTarget(data.host, data.code);
    }
    const url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash
      || url.searchParams.getAll('pairing_code').length !== 1 || url.searchParams.getAll('backend').length > 1) return null;
    // A PWA link carries the backend separately from the app's own address.
    return pairingTarget(url.searchParams.get('backend') ?? url.origin, url.searchParams.get('pairing_code') ?? '');
  } catch {
    return null;
  }
}

/** Optional PWA origin makes the link open the phone app before exchanging the code. */
export function pairingLink(target: PairQR, pwaUrl?: string): string {
  const normalized = pairingTarget(target.host, target.code);
  const url = new URL(pwaUrl ? backendHost(pwaUrl) : normalized.host);
  if (pwaUrl) url.searchParams.set('backend', normalized.host);
  url.searchParams.set('pairing_code', normalized.code);
  return url.toString();
}
