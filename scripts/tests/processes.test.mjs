import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let common;
let dir;
const pidFile = () => path.join(dir, 'pids.json');

beforeAll(async () => {
  dir = mkdtempSync(path.join(tmpdir(), 'janus-pids-'));
  process.env.JANUS_STATE_DIR = dir; // must be set before the module is first imported
  common = await import('../lib/common.mjs');
});
afterAll(() => { rmSync(dir, { recursive: true, force: true }); delete process.env.JANUS_STATE_DIR; });
beforeEach(() => { rmSync(pidFile(), { force: true }); });

const NOW = 1_800_000_000_000;

describe('pid records', () => {
  it('round-trips records and removes the file when empty', () => {
    common.recordProcess('backend', 111, NOW, 7, NOW - 50);
    common.recordProcess('agent', 222, NOW + 5, 7, NOW - 50);
    expect(common.readRecords()).toEqual({
      backend: { pid: 111, startedAt: NOW, owner: 7, ownerStartedAt: NOW - 50 },
      agent: { pid: 222, startedAt: NOW + 5, owner: 7, ownerStartedAt: NOW - 50 },
    });
    expect(common.readPids()).toEqual({ backend: 111, agent: 222 });
    common.forgetProcesses(['backend', 'agent']);
    expect(existsSync(pidFile())).toBe(false);
  });

  it('reads the older name -> pid shape (unverifiable) and ignores junk', () => {
    writeFileSync(pidFile(), JSON.stringify({ backend: 42, bad: 'x', worse: null, ok: { pid: 7, startedAt: 9 } }));
    expect(common.readRecords()).toEqual({
      backend: { pid: 42, startedAt: 0, owner: 0, ownerStartedAt: 0 },
      ok: { pid: 7, startedAt: 9, owner: 0, ownerStartedAt: 0 },
    });
    writeFileSync(pidFile(), 'not json');
    expect(common.readRecords()).toEqual({});
  });
});

describe('isRecordedProcess', () => {
  const record = { pid: 500, startedAt: NOW };
  it('requires a live process that started when we recorded it', () => {
    expect(common.isRecordedProcess(record, { alive: () => true, createdAt: () => NOW + 1000 })).toBe(true);
    expect(common.isRecordedProcess(record, { alive: () => false, createdAt: () => NOW })).toBe(false);
  });
  it('rejects a recycled PID (a different, later process now has it)', () => {
    expect(common.isRecordedProcess(record, { alive: () => true, createdAt: () => NOW + 3_600_000 })).toBe(false);
    expect(common.isRecordedProcess(record, { alive: () => true, createdAt: () => null })).toBe(false);
  });
  it('rejects records without a start time', () => {
    expect(common.isRecordedProcess({ pid: 500, startedAt: 0 }, { alive: () => true, createdAt: () => NOW })).toBe(false);
  });
});

describe('killLeftovers', () => {
  it('kills only verified leftovers, leaves strangers alone, and forgets everything it handled', () => {
    common.recordProcess('backend', 1, NOW);       // alive and verified -> killed
    common.recordProcess('agent', 2, NOW);         // dead -> just forgotten
    common.recordProcess('imessage', 3, NOW);      // alive but recycled PID -> left alone
    common.recordProcess('studio', 4, NOW);        // kept by the caller
    const kill = vi.fn();
    const alive = (pid) => pid !== 2;
    const createdAt = (pid) => (pid === 3 ? NOW + 7_200_000 : NOW);
    const lines = common.killLeftovers({ keep: ['studio'], alive, createdAt, kill });
    expect(kill.mock.calls).toEqual([[1]]);
    expect(lines).toHaveLength(2);
    expect(lines[0]).toContain('Stopped leftover backend');
    expect(lines[1]).toContain('Left PID 3');
    expect(Object.keys(common.readRecords())).toEqual(['studio']);
  });

  it('does nothing when there are no records', () => {
    const kill = vi.fn();
    expect(common.killLeftovers({ kill })).toEqual([]);
    expect(kill).not.toHaveBeenCalled();
  });
});

describe('otherOrchestrator', () => {
  const alive = () => true;
  const created = (pid) => (pid === 900 ? NOW + 100 : NOW);

  it('sees a healthy stack owned by another running start.mjs', () => {
    common.recordProcess('backend', 10, NOW, 900, NOW);
    expect(common.otherOrchestrator({ alive, createdAt: created, self: 1 })).toMatchObject({ name: 'backend', owner: 900 });
  });

  it('ignores this process, dead owners, recycled owner PIDs, and legacy records', () => {
    common.recordProcess('backend', 10, NOW, 900, NOW);
    expect(common.otherOrchestrator({ alive, createdAt: created, self: 900 })).toBeNull();
    expect(common.otherOrchestrator({ alive: () => false, createdAt: created, self: 1 })).toBeNull();
    expect(common.otherOrchestrator({ alive, createdAt: () => NOW + 7_200_000, self: 1 })).toBeNull();
    writeFileSync(pidFile(), JSON.stringify({ backend: 10 }));
    expect(common.otherOrchestrator({ alive, createdAt: created, self: 1 })).toBeNull();
  });

  it('does not count a solo agent: a full start replaces that one', () => {
    common.recordProcess('agent-solo', 11, NOW, 901, NOW);
    expect(common.otherOrchestrator({ alive, createdAt: () => NOW, self: 1 })).toBeNull();
  });
});

describe('liveAgent', () => {
  it('finds a verified agent from npm start or the Studio button, and nothing else', () => {
    const live = { alive: () => true, createdAt: () => NOW };
    common.recordProcess('backend', 10, NOW);
    expect(common.liveAgent(live)).toBeNull();
    common.recordProcess('agent', 11, NOW);
    expect(common.liveAgent(live)).toMatchObject({ name: 'agent', pid: 11 });
    common.forgetProcesses(['agent']);
    common.recordProcess('agent-solo', 12, NOW);
    expect(common.liveAgent(live)).toMatchObject({ name: 'agent-solo', pid: 12 });
    expect(common.liveAgent({ alive: () => false, createdAt: () => NOW })).toBeNull();
    expect(common.liveAgent({ alive: () => true, createdAt: () => NOW + 9_999_999 })).toBeNull();
  });
});

describe('killTree (real processes)', () => {
  const waitUntil = async (condition, ms = 8000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { if (condition()) return true; await new Promise((resolve) => setTimeout(resolve, 100)); }
    return condition();
  };

  it('kills a parent and the grandchild it spawned', async () => {
    const grandchildPidFile = path.join(dir, 'grandchild.pid');
    const script = `
      const { spawn } = require('node:child_process');
      const fs = require('node:fs');
      const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
      fs.writeFileSync(${JSON.stringify(grandchildPidFile)}, String(child.pid));
      setInterval(() => {}, 1000);
    `;
    const parent = spawn(process.execPath, ['-e', script], { stdio: 'ignore', detached: process.platform !== 'win32' });
    expect(await waitUntil(() => existsSync(grandchildPidFile))).toBe(true);
    const grandchild = Number(readFileSync(grandchildPidFile, 'utf8'));
    expect(common.isAlive(parent.pid)).toBe(true);
    expect(common.isAlive(grandchild)).toBe(true);

    common.killTree(parent.pid);

    expect(await waitUntil(() => !common.isAlive(parent.pid))).toBe(true);
    expect(await waitUntil(() => !common.isAlive(grandchild))).toBe(true);
  }, 20_000);

  it('reports a real, live process as started at about the time we spawned it', async () => {
    const before = Date.now();
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
    try {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const created = common.processCreatedAt(child.pid);
      expect(created).not.toBeNull();
      expect(Math.abs(created - before)).toBeLessThan(30_000);
      expect(common.isRecordedProcess({ pid: child.pid, startedAt: before })).toBe(true);
      expect(common.isRecordedProcess({ pid: child.pid, startedAt: before - 3_600_000 })).toBe(false);
    } finally {
      common.killTree(child.pid);
    }
  }, 20_000);
});

describe('leftovers and duplicate agents with real processes', () => {
  const sleeper = () => spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore', detached: process.platform !== 'win32' });
  const waitUntil = async (condition, ms = 8000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { if (condition()) return true; await new Promise((resolve) => setTimeout(resolve, 100)); }
    return condition();
  };

  it('killLeftovers stops a recorded process that is really still running, and only that one', async () => {
    const ours = sleeper();
    const stranger = sleeper();
    try {
      await new Promise((resolve) => setTimeout(resolve, 500));
      common.recordProcess('backend', ours.pid, Date.now());
      common.recordProcess('agent', stranger.pid, Date.now() - 3_600_000); // wrong start time: a recycled PID in real life
      const lines = common.killLeftovers();
      expect(lines.join(' | ')).toContain(`Stopped leftover backend from an earlier run (PID ${ours.pid})`);
      expect(lines.join(' | ')).toContain(`Left PID ${stranger.pid}`);
      expect(await waitUntil(() => !common.isAlive(ours.pid))).toBe(true);
      expect(common.isAlive(stranger.pid)).toBe(true);
      expect(common.readRecords()).toEqual({});
    } finally {
      common.killTree(ours.pid);
      common.killTree(stranger.pid);
    }
  }, 20_000);

  it('start.mjs --only=agent refuses to start while a recorded agent is alive', async () => {
    const agent = sleeper();
    try {
      await new Promise((resolve) => setTimeout(resolve, 500));
      common.recordProcess('agent', agent.pid, Date.now());
      const script = fileURLToPath(new URL('../start.mjs', import.meta.url));
      const result = await new Promise((resolve) => {
        const run = spawn(process.execPath, [script, '--only=agent'], { env: { ...process.env, JANUS_STATE_DIR: dir }, stdio: ['ignore', 'pipe', 'pipe'] });
        let output = '';
        run.stdout.on('data', (chunk) => { output += chunk; });
        run.stderr.on('data', (chunk) => { output += chunk; });
        run.on('exit', (code) => resolve({ code, output }));
      });
      expect(result.code).toBe(1);
      expect(result.output).toContain(`An agent is already running (PID ${agent.pid})`);
      expect(common.isAlive(agent.pid)).toBe(true);
    } finally {
      common.killTree(agent.pid);
    }
  }, 30_000);
});
