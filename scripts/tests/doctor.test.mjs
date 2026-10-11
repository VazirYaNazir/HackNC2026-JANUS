import { afterEach, describe, expect, it } from 'vitest';
import { aiChecks, imessageChecks } from '../doctor.mjs';

const saved = { ...process.env };
afterEach(() => { process.env = { ...saved }; });

describe('aiChecks', () => {
  it('only ever warns, because built-in controls work without AI widgets', () => {
    delete process.env.GEMINI_API_KEY; delete process.env.GOOGLE_API_KEY; delete process.env.AGENT_ISOLATION_ADAPTER;
    const results = aiChecks();
    expect(results.every((result) => result.level === 'warn')).toBe(true);
    expect(results.map((result) => result.name)).toContain('AI widgets · isolation adapter');
  });
  it('is quiet about the isolation adapter once one is configured', () => {
    process.env.AGENT_ISOLATION_ADAPTER = 'my_app.worker:create_boundary';
    expect(aiChecks().map((result) => result.name)).not.toContain('AI widgets · isolation adapter');
  });
  it('is quiet about the key when GEMINI_API_KEY is set', () => {
    process.env.GEMINI_API_KEY = 'AIzaSyExampleExampleExample1234';
    expect(aiChecks().map((result) => result.name)).not.toContain('AI widgets · Gemini key');
  });
});

describe('imessageChecks', () => {
  const clear = () => { for (const key of ['SPECTRUM_PROJECT_ID', 'SPECTRUM_PROJECT_SECRET', 'IMESSAGE_ALLOWED_SENDERS', 'MESSAGING_SERVICE_TOKEN']) delete process.env[key]; };
  const names = (env = new Map()) => imessageChecks(env).map((result) => result.name);

  it('warns (never blocks) about missing credentials, allowlist and token', () => {
    clear();
    const results = imessageChecks(new Map());
    expect(results.every((result) => result.level === 'warn')).toBe(true);
    expect(names()).toEqual(expect.arrayContaining(['iMessage · Spectrum credentials', 'iMessage · allowlist', 'iMessage · service token']));
  });
  it('treats the example placeholder and malformed numbers as an empty allowlist', () => {
    clear();
    for (const value of ['+1XXXXXXXXXX', '5551234567', ' , ']) {
      process.env.IMESSAGE_ALLOWED_SENDERS = value;
      expect(names()).toContain('iMessage · allowlist');
    }
  });
  it('ignores example placeholders in backend/.env', () => {
    clear();
    const placeholders = new Map([['SPECTRUM_PROJECT_ID', '...'], ['SPECTRUM_PROJECT_SECRET', '...'], ['IMESSAGE_ALLOWED_SENDERS', '+1XXXXXXXXXX']]);
    expect(names(placeholders)).toEqual(expect.arrayContaining(['iMessage · Spectrum credentials', 'iMessage · allowlist']));
  });
  it('reads values from backend/.env when the process environment has none', () => {
    clear();
    const file = new Map([['SPECTRUM_PROJECT_ID', 'p'], ['SPECTRUM_PROJECT_SECRET', 's'], ['IMESSAGE_ALLOWED_SENDERS', '+15551234567'], ['MESSAGING_SERVICE_TOKEN', 't'.repeat(40)]]);
    expect(names(file)).toEqual([]);
  });
  it('is quiet when everything is configured', () => {
    clear();
    Object.assign(process.env, { SPECTRUM_PROJECT_ID: 'p', SPECTRUM_PROJECT_SECRET: 's', IMESSAGE_ALLOWED_SENDERS: '+1 (555) 123-4567', MESSAGING_SERVICE_TOKEN: 't'.repeat(40) });
    expect(imessageChecks(new Map())).toEqual([]);
  });
});
