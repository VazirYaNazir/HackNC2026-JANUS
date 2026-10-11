import { afterEach, describe, expect, it } from 'vitest';
import { aiChecks } from '../doctor.mjs';

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
