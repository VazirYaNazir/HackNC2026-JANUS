import { describe, expect, it } from 'vitest';
import { interpretTigerCheck, tigerChecks } from '../doctor.mjs';
import { tigerUrlFrom } from '../setup.mjs';

const line = (report) => `some uv noise\n${JSON.stringify(report)}\n`;

describe('interpretTigerCheck', () => {
  it('is silent when Tiger is not configured or fully set up', () => {
    expect(interpretTigerCheck(line({ configured: false }))).toEqual([]);
    expect(interpretTigerCheck(line({ configured: true, reachable: true, schema: true, missing: [] }))).toEqual([]);
  });

  it('warns (never errors) when the database is unreachable', () => {
    const [result] = interpretTigerCheck(line({ configured: true, reachable: false, error: 'OperationalError', missing: [] }));
    expect(result.level).toBe('warn');
    expect(result.name).toBe('Tiger Data · connection');
    expect(result.message).toContain('OperationalError');
    expect(result.message).toContain('dropped');
  });

  it('warns about a missing schema and says how to create it', () => {
    const [result] = interpretTigerCheck(line({ configured: true, reachable: true, schema: false, missing: ['metrics_1m', 'action_events'] }));
    expect(result.level).toBe('warn');
    expect(result.name).toBe('Tiger Data · schema');
    expect(result.message).toContain('metrics_1m, action_events');
    expect(result.fix).toContain('npm run setup');
  });

  it('warns when the check itself produced garbage', () => {
    for (const output of ['', 'Traceback (most recent call last)', '{not json']) {
      const [result] = interpretTigerCheck(output);
      expect(result.level).toBe('warn');
      expect(result.name).toBe('Tiger Data');
    }
  });
});

describe('tigerChecks', () => {
  it('does nothing at all (and does not start Python) when no URL is configured', () => {
    const saved = process.env.TIGER_DATABASE_URL;
    delete process.env.TIGER_DATABASE_URL;
    try {
      expect(tigerChecks(new Map())).toEqual([]);
      expect(tigerChecks(new Map([['TIGER_DATABASE_URL', '   ']]))).toEqual([]);
    } finally {
      if (saved !== undefined) process.env.TIGER_DATABASE_URL = saved;
    }
  });
});

describe('tigerUrlFrom (setup)', () => {
  it('prefers the process environment, then backend/.env, and treats empty as off', () => {
    expect(tigerUrlFrom('TIGER_DATABASE_URL=postgres://file', {})).toBe('postgres://file');
    expect(tigerUrlFrom('TIGER_DATABASE_URL=postgres://file', { TIGER_DATABASE_URL: 'postgres://env' })).toBe('postgres://env');
    expect(tigerUrlFrom('TIGER_DATABASE_URL=', {})).toBe('');
    expect(tigerUrlFrom('OTHER=1', {})).toBe('');
    expect(tigerUrlFrom('TIGER_DATABASE_URL=  postgres://padded  ', {})).toBe('postgres://padded');
  });
});
