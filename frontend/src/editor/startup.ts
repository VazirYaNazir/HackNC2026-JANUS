import { backendHost, type Connection } from '@janus/client';

function loopbackOrigin(value: string): string {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
    || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('The startup connection must use a local HTTP address.');
  }
  return url.origin;
}

/** Consume a launcher's one-use handoff. Credentials never appear in the URL or browser storage. */
export async function startupConnection(location: Pick<Location, 'origin' | 'hash' | 'pathname' | 'search'>, history: Pick<History, 'replaceState'>): Promise<Connection | null> {
  const fragment = new URLSearchParams(location.hash.slice(1));
  if (!fragment.has('janus-start')) return null;
  // Remove the nonce before performing any network request, including when validation fails.
  history.replaceState(null, '', location.pathname + location.search);
  const origin = loopbackOrigin(fragment.get('janus-start') ?? '');
  loopbackOrigin(location.origin);
  const nonce = fragment.get('nonce');
  if (!nonce || !/^[A-Za-z0-9_-]{16,256}$/.test(nonce)) throw new Error('Invalid startup connection code.');
  const response = await fetch(`${origin}/bootstrap`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nonce }),
    credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error('The startup connection expired. Restart JANUS or connect manually.');
  const connection: unknown = await response.json();
  if (!connection || typeof connection !== 'object' || !('host' in connection) || !('token' in connection)
    || typeof connection.host !== 'string' || typeof connection.token !== 'string' || !connection.token.trim() || connection.token.length > 8192) {
    throw new Error('Invalid startup connection response.');
  }
  return { host: backendHost(loopbackOrigin(connection.host)), token: connection.token };
}
