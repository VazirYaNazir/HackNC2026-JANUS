import { afterEach, describe, expect, it, vi } from 'vitest';
import { startupConnection } from '../src/editor/startup';

const nonce = 'startup_nonce_1234567890';
const location = (origin = 'http://localhost:5173', bootstrap = 'http://127.0.0.1:12345', code = nonce) => ({
  origin, pathname: '/', search: '?preview=1', hash: `#janus-start=${encodeURIComponent(bootstrap)}&nonce=${code}`,
});
afterEach(() => vi.unstubAllGlobals());

describe('launcher one-use Studio connection', () => {
  it('removes the handoff before fetching and accepts only a valid local credential', async () => {
    const history = { replaceState: vi.fn() };
    const fetch = vi.fn(async () => {
      expect(history.replaceState).toHaveBeenCalledWith(null, '', '/?preview=1');
      return { ok: true, json: async () => ({ host: 'http://127.0.0.1:8000', token: 'controller' }) };
    });
    vi.stubGlobal('fetch', fetch);
    await expect(startupConnection(location(), history)).resolves.toEqual({ host: 'http://127.0.0.1:8000', token: 'controller' });
    expect(fetch).toHaveBeenCalledWith('http://127.0.0.1:12345/bootstrap', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ nonce }), credentials: 'omit', redirect: 'error',
    }));
  });
  it('leaves unrelated fragments alone', async () => {
    const history = { replaceState: vi.fn() };
    await expect(startupConnection({ ...location(), hash: '#gallery' }, history)).resolves.toBeNull();
    expect(history.replaceState).not.toHaveBeenCalled();
  });
  it.each(['https://example.com', 'http://192.168.1.20:8000', 'http://127.0.0.1.evil.example', 'http://user:password@localhost:8000', 'http://localhost:8000/path', 'http://localhost:8000?nonce=evil', 'http://localhost:8000/#evil'])('rejects non-local or malformed handoff origin %s without fetching', async origin => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const history = { replaceState: vi.fn() };
    await expect(startupConnection(location('http://localhost:5173', origin), history)).rejects.toThrow();
    expect(history.replaceState).toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
  });
  it('rejects a remote Studio origin and malformed nonce', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch); const history = { replaceState: vi.fn() };
    await expect(startupConnection(location('https://studio.example'), history)).rejects.toThrow();
    await expect(startupConnection(location('http://localhost:5173', 'http://localhost:12345', 'short'), history)).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([null, {}, { host: 'http://localhost:8000', token: '' }, { host: 'https://evil.example', token: 'secret' }, { host: 'http://localhost:8000', token: 1 }])('rejects invalid response %#', async connection => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => connection }));
    await expect(startupConnection(location(), { replaceState: vi.fn() })).rejects.toThrow();
  });
  it('provides a usable recovery message when the nonce has expired', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
    await expect(startupConnection(location(), { replaceState: vi.fn() })).rejects.toThrow('Restart JANUS or connect manually');
  });
});
