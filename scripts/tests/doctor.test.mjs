import { afterEach, describe, expect, it, vi } from 'vitest';
import { aiChecks, imessageChecks, workerStatus } from '../doctor.mjs';

const saved = { ...process.env };
afterEach(() => { process.env = { ...saved }; });

describe('aiChecks', () => {
  const clear = () => { for (const key of ['GEMINI_API_KEY', 'GOOGLE_API_KEY', 'AGENT_ISOLATION_ADAPTER']) delete process.env[key]; };
  const unavailable = { available: false, reason: 'DOCKER_NOT_INSTALLED', execution_target: null };
  const checks = (env = new Map(), report = unavailable) => aiChecks(env, { probe: () => report });
  it('only ever warns, because built-in controls work without AI widgets', () => {
    clear();
    const results = checks();
    expect(results.every((result) => result.level === 'warn')).toBe(true);
    expect(results.map((result) => result.name)).toContain('AI widgets · execution worker');
    expect(results.at(-1).fix).toContain('Install Docker Desktop');
  });
  it('configuration alone cannot claim that a custom adapter is ready', () => {
    clear();
    process.env.AGENT_ISOLATION_ADAPTER = 'my_app.worker:create_boundary';
    expect(checks().map((result) => result.name)).toContain('AI widgets · execution worker');
    expect(checks(new Map(), { available: true }).map((result) => result.name)).not.toContain('AI widgets · execution worker');
  });
  it('is quiet about the key when GEMINI_API_KEY is set', () => {
    clear();
    process.env.GEMINI_API_KEY = 'AIzaSyExampleExampleExample1234';
    expect(checks().map((result) => result.name)).not.toContain('AI widgets · Gemini key');
  });
  it('reads generation credentials and an isolation worker from backend/.env', () => {
    clear();
    const file = new Map([['GOOGLE_API_KEY', 'fixture-key'], ['AGENT_ISOLATION_ADAPTER', 'my_app.worker:create_boundary']]);
    expect(checks(file, { available: true })).toEqual([]);
  });
  it('an explicit empty process value overrides a configured file value', () => {
    clear();
    Object.assign(process.env, { GEMINI_API_KEY: '', GOOGLE_API_KEY: '', AGENT_ISOLATION_ADAPTER: '' });
    const file = new Map([['GEMINI_API_KEY', 'file-key'], ['GOOGLE_API_KEY', 'other-file-key'], ['AGENT_ISOLATION_ADAPTER', 'my_app.worker:create_boundary']]);
    expect(checks(file).map((result) => result.name)).toEqual(['AI widgets · Gemini key', 'AI widgets · execution worker']);
  });
  it('explains that generation remains available with a key when execution has no adapter', () => {
    clear();
    process.env.GEMINI_API_KEY = 'fixture-key';
    const results = checks();
    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('AI widgets · execution worker');
    expect(results[0].message).toMatch(/generate and review C source/);
    expect(results[0].message).toMatch(/native controls remain available/);
  });
  it('uses the alternate key when the Gemini-specific process key is empty', () => {
    clear();
    process.env.GEMINI_API_KEY = '';
    const file = new Map([['GEMINI_API_KEY', 'overridden-key'], ['GOOGLE_API_KEY', 'fallback-key']]);
    expect(checks(file).map((result) => result.name)).not.toContain('AI widgets · Gemini key');
  });

  it('uses the actual worker report and explains daemon, image, engine and resource failures', () => {
    clear();
    process.env.GEMINI_API_KEY = 'fixture-key';
    for (const [reason, remedy] of [['DOCKER_NOT_RUNNING', 'Start Docker Desktop'], ['WORKER_IMAGE_MISSING', 'included worker image'],
      ['DOCKER_LINUX_ENGINE_REQUIRED', 'Linux containers'], ['WORKER_ENFORCEMENT_UNAVAILABLE', 'cgroup v2']]) {
      expect(checks(new Map(), { available: false, reason })[0].fix).toContain(remedy);
    }
  });
});

describe('workerStatus', () => {
  it('probes the locked agent environment and preserves explicit empty process overrides', () => {
    process.env.AGENT_ISOLATION_ADAPTER = '';
    const run = vi.fn(() => ({ code: 0, stdout: JSON.stringify({ available: false, reason: 'WORKER_IMAGE_MISSING', execution_target: null }) }));
    const report = workerStatus(new Map([['AGENT_ISOLATION_ADAPTER', 'custom.worker:create_boundary'], ['GEMINI_API_KEY', 'private-key']]), { run });
    expect(report.reason).toBe('WORKER_IMAGE_MISSING');
    const [command, args, options] = run.mock.calls[0];
    expect(command).toBe('uv');
    expect(args).toEqual(expect.arrayContaining(['--no-sync', '--directory', 'agent.main', 'worker', 'status']));
    expect(args.join(' ')).not.toContain('private-key');
    expect(options.env.AGENT_ISOLATION_ADAPTER).toBe('');
    expect(options.env.GEMINI_API_KEY).toBe(process.env.GEMINI_API_KEY ?? 'private-key');
    expect(options.timeout).toBe(60_000);
  });

  it('allows bounded image preparation and interprets unavailable JSON even with a failing exit status', () => {
    const run = vi.fn(() => ({ code: 1, stdout: '{"available":false,"reason":"DOCKER_NOT_INSTALLED","execution_target":null}' }));
    expect(workerStatus(new Map(), { action: 'prepare', run }).reason).toBe('DOCKER_NOT_INSTALLED');
    expect(run.mock.calls[0][1].at(-1)).toBe('prepare');
    expect(run.mock.calls[0][2].timeout).toBe(240_000);
  });

  it('malformed output and failed ready claims become safe diagnostics', () => {
    for (const result of [{ code: 1, stdout: 'private-credential', stderr: 'private-credential' },
      { code: 0, stdout: '{"available":"yes"}' }, { code: 1, stdout: '{"available":true}' }]) {
      const report = workerStatus(new Map(), { run: () => result });
      expect(report).toEqual({ available: false, reason: 'WORKER_PROBE_FAILED', execution_target: null });
      expect(JSON.stringify(report)).not.toContain('private-credential');
    }
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
  it('automatic startup is quiet when iMessage has no configuration', () => {
    clear();
    expect(imessageChecks(new Map(), false)).toEqual([]);
  });
  it('automatic startup diagnoses partially configured iMessage', () => {
    clear();
    const file = new Map([['SPECTRUM_PROJECT_ID', 'project']]);
    expect(imessageChecks(file, false).map((result) => result.name)).toEqual(expect.arrayContaining(['iMessage · Spectrum credentials', 'iMessage · allowlist', 'iMessage · service token']));
  });
  it('explicit empty process values override valid file credentials and numbers', () => {
    clear();
    Object.assign(process.env, { SPECTRUM_PROJECT_ID: '', SPECTRUM_PROJECT_SECRET: '', IMESSAGE_ALLOWED_SENDERS: '', MESSAGING_SERVICE_TOKEN: '' });
    const file = new Map([['SPECTRUM_PROJECT_ID', 'p'], ['SPECTRUM_PROJECT_SECRET', 's'], ['IMESSAGE_ALLOWED_SENDERS', '+15551234567'], ['MESSAGING_SERVICE_TOKEN', 't'.repeat(40)]]);
    expect(names(file)).toEqual(expect.arrayContaining(['iMessage · Spectrum credentials', 'iMessage · allowlist', 'iMessage · service token']));
    expect(imessageChecks(file, false)).toEqual([]);
  });
  it('diagnoses invalid allowlist entries while keeping valid entries usable', () => {
    clear();
    const results = imessageChecks(new Map([['SPECTRUM_PROJECT_ID', 'p'], ['SPECTRUM_PROJECT_SECRET', 's'], ['IMESSAGE_ALLOWED_SENDERS', '+15551234567, invalid-person'], ['MESSAGING_SERVICE_TOKEN', 't'.repeat(40)]]));
    expect(results).toHaveLength(1);
    expect(results[0].name).toBe('iMessage · allowlist');
    expect(results[0].message).toContain('Valid entries remain allowed');
    expect(results[0].message).not.toContain('invalid-person');
  });
  it('treats startup project placeholders as missing settings', () => {
    clear();
    for (const value of ['<project-id>', 'your-project_id', 'replace_project-secret', 'example_secret']) {
      const file = new Map([['SPECTRUM_PROJECT_ID', value], ['SPECTRUM_PROJECT_SECRET', 'fixture-secret']]);
      expect(names(file)).toContain('iMessage · Spectrum credentials');
    }
  });
});
