import { describe, expect, it } from 'vitest';
import { fillGenerated, looksLikeApiKey, mergeEnv, setEnvValue } from '../setup.mjs';
import { parseEnv } from '../lib/common.mjs';

const example = ['# comment', 'ADMIN_SECRET=', 'APP_ENV=development', 'AGENT_DEVICES_JSON=[]', 'PWA_PUBLIC_URL=', 'NEW_KEY=fresh', '# COMMENTED=1'].join('\n');

describe('mergeEnv', () => {
  it('adds only the keys the file lacks and never alters existing values', () => {
    const existing = 'ADMIN_SECRET=keepme\nAPP_ENV=production';
    const { text, added } = mergeEnv(existing, example);
    expect(added).toEqual(['AGENT_DEVICES_JSON', 'PWA_PUBLIC_URL', 'NEW_KEY']);
    const env = parseEnv(text);
    expect(env.get('ADMIN_SECRET')).toBe('keepme');
    expect(env.get('APP_ENV')).toBe('production');
    expect(env.get('NEW_KEY')).toBe('fresh');
    expect(text).not.toContain('COMMENTED');
  });

  it('is a no-op when nothing is missing', () => {
    const { text, added } = mergeEnv(example, example);
    expect(added).toEqual([]);
    expect(text).toBe(example);
  });
});

describe('fillGenerated', () => {
  it('generates secrets and a device where they are empty, and defaults the PWA URL', () => {
    const { text, filled } = fillGenerated(example, { pwaUrl: 'https://pwa.example.app/' });
    const env = parseEnv(text);
    expect(filled).toEqual(['ADMIN_SECRET', 'AGENT_DEVICES_JSON', 'PWA_PUBLIC_URL']);
    expect(env.get('ADMIN_SECRET').length).toBeGreaterThanOrEqual(32);
    const [device] = JSON.parse(env.get('AGENT_DEVICES_JSON'));
    expect(device.device_id).toBe('my-laptop');
    expect(device.agent_token).not.toBe(device.controller_token);
    expect(device.agent_token.length).toBeGreaterThanOrEqual(32);
    expect(env.get('PWA_PUBLIC_URL')).toBe('https://pwa.example.app');
  });

  it('keeps values that are already set, so re-running setup changes nothing', () => {
    const configured = 'ADMIN_SECRET=abc\nAGENT_DEVICES_JSON=[{"device_id":"x"}]\nPWA_PUBLIC_URL=https://mine.app';
    const { text, filled } = fillGenerated(configured, { pwaUrl: 'https://other.app' });
    expect(filled).toEqual([]);
    expect(text).toBe(configured);
  });

  it('leaves the PWA URL empty when no default is known', () => {
    expect(parseEnv(fillGenerated(example, { pwaUrl: '' }).text).get('PWA_PUBLIC_URL')).toBe('');
  });
});

describe('Gemini key helpers', () => {
  it('sets the key on its existing line and leaves the rest alone', () => {
    const text = 'A=1\nGEMINI_API_KEY=\nB=2';
    expect(setEnvValue(text, 'GEMINI_API_KEY', 'AIza-example-key-1234')).toBe('A=1\nGEMINI_API_KEY=AIza-example-key-1234\nB=2');
  });
  it('does not treat $ sequences in a key as replacement patterns', () => {
    expect(setEnvValue('GEMINI_API_KEY=', 'GEMINI_API_KEY', 'ab$&cd$1efghijklmnop')).toBe('GEMINI_API_KEY=ab$&cd$1efghijklmnop');
  });
  it('accepts plausible keys and rejects whitespace or short input', () => {
    expect(looksLikeApiKey('AIzaSyExampleExampleExample1234')).toBe(true);
    expect(looksLikeApiKey('short')).toBe(false);
    expect(looksLikeApiKey('has a space in it 1234567890')).toBe(false);
  });
});
