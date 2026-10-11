import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { pathKey, tunnelUrlFrom } from '../lib/common.mjs';

describe('tunnelUrlFrom', () => {
  it('reads the URL out of the line the backend logs through uvicorn', () => {
    expect(tunnelUrlFrom('INFO:     Cloudflare Tunnel active at: https://plans-gap-laura-preceding.trycloudflare.com')).toBe('https://plans-gap-laura-preceding.trycloudflare.com');
    expect(tunnelUrlFrom('Cloudflare Tunnel active at: https://a1-b2.trycloudflare.com extra words')).toBe('https://a1-b2.trycloudflare.com');
  });

  it('ignores other lines, including cloudflared chatter and look-alike hosts', () => {
    for (const line of [
      '', 'INFO:     Application startup complete.',
      '| https://plans-gap-laura-preceding.trycloudflare.com |',                    // cloudflared's own banner is not the backend's confirmation
      'Cloudflare Tunnel active at: https://evil.example.com/.trycloudflare.com',
      'Cloudflare Tunnel active at: http://insecure.trycloudflare.com',
      'Could not start Cloudflare tunnel: cloudflared executable not found in PATH.',
    ]) {
      expect(tunnelUrlFrom(line)).toBeNull();
    }
  });
});

describe('pathKey', () => {
  it('finds the variable under whatever casing the platform uses, so overriding it replaces it', () => {
    expect(pathKey({ Path: 'x', OTHER: 'y' })).toBe('Path');
    expect(pathKey({ PATH: 'x' })).toBe('PATH');
    expect(pathKey({ path: 'x' })).toBe('path');
    expect(pathKey({})).toBe('PATH');
  });
});

describe('start.mjs wiring for the tunnel', () => {
  const source = readFileSync(new URL('../start.mjs', import.meta.url), 'utf8');
  const scripts = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8')).scripts;

  it('start:demo asks for the tunnel', () => {
    expect(scripts['start:demo']).toBe('node scripts/start.mjs --imessage --screen --tunnel');
  });

  it('shows the tunnel URL when it comes up, and says loudly when it did not', () => {
    expect(source).toContain('onLine: (line) =>');
    expect(source).toContain('Cloudflare tunnel is up: ${url}');
    expect(source).toContain('The Cloudflare tunnel did not come up, so phone pairing will not work');
  });

  it('puts the pinned cloudflared first on the real PATH variable, not a second copy of it', () => {
    expect(source).toContain('backendEnv[pathKey()] =');
    expect(source).not.toContain('backendEnv.PATH');
  });
});
