import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { pathKey, tunnelUrlFrom } from '../lib/common.mjs';
import { launcherCommand, normalizeLauncherArgs } from '../start.mjs';

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
  const scripts = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8')).scripts;

  it('start:demo asks for the tunnel', () => {
    expect(scripts['start:demo']).toBe('node scripts/start.mjs --imessage --screen --tunnel');
  });

  it('delegates explicit tunnel requests to the common Python launcher', () => {
    const [command, args] = launcherCommand(normalizeLauncherArgs(['--tunnel'], {}));
    expect(command).toBe('uv');
    expect(args.at(-1)).toBe('--tunnel');
    expect(args.some((value) => value.endsWith('start.py'))).toBe(true);
  });

  it('preserves an explicit executable path containing spaces as one argument', () => {
    const [, args] = launcherCommand(normalizeLauncherArgs(['--cloudflared', 'C:/Program Files/cloudflared.exe'], {}));
    expect(args.slice(-2)).toEqual(['--cloudflared', 'C:/Program Files/cloudflared.exe']);
  });
});
