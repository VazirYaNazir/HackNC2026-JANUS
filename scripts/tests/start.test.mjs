import path from 'node:path';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { isAlive, ROOT } from '../lib/common.mjs';
import { createOutputRelay, launcherCommand, normalizeLauncherArgs, ownedRecordedProcesses, parseProcessMarker } from '../start.mjs';

describe('the shared Python launcher command', () => {
  it('uses the backend locked environment and forwards all arguments unchanged', () => {
    const args = ['--only=agent', '--startup-approval-stdin', '--device-id', 'selected-laptop'];
    expect(launcherCommand(args)).toEqual(['uv', ['run', '--locked', '--project', path.join(ROOT, 'backend'), 'python', path.join(ROOT, 'start.py'), ...args]]);
    expect(args).toEqual(['--only=agent', '--startup-approval-stdin', '--device-id', 'selected-laptop']);
  });
});

describe('npm and PowerShell argument compatibility', () => {
  it('recovers swallowed booleans, negative flags and values without including unrelated secrets', () => {
    const args = normalizeLauncherArgs([], {
      npm_config_imessage: 'true', npm_config_tunnel: 'true', npm_config_screen: 'false',
      npm_config_agent: 'false', npm_config_studio: 'none', npm_config_backend_port: '18000',
      GEMINI_API_KEY: 'private-key', SPECTRUM_PROJECT_SECRET: 'private-sidecar-key',
    });
    expect(args).toEqual(expect.arrayContaining(['--imessage', '--tunnel', '--no-screen', '--no-agent', '--studio=none', '--backend-port=18000']));
    expect(args.join(' ')).not.toContain('private');
  });

  it('preserves explicit separated and inline values over npm configuration', () => {
    const original = ['--studio', 'browser', '--device-id=one', '--backend-port', '18000'];
    expect(normalizeLauncherArgs(original, { npm_config_studio: 'electron', npm_config_device_id: 'two', npm_config_backend_port: '8000' })).toEqual(original);
    expect(original).toEqual(['--studio', 'browser', '--device-id=one', '--backend-port', '18000']);
  });

  it('explicit modes and screen choices win over inherited opposing npm settings', () => {
    expect(normalizeLauncherArgs(['--studio=none', '--no-screen', '--local-only'], {
      npm_config_electron: 'true', npm_config_screen: 'true', npm_config_tunnel: 'true',
    })).toEqual(['--studio=none', '--no-screen', '--local-only']);
    expect(normalizeLauncherArgs(['--screen'], { npm_config_screen: 'false' })).toEqual(['--screen']);
  });

  it('the desktop solo-agent request is not disabled by inherited full-stack no-agent config', () => {
    expect(normalizeLauncherArgs(['--only=agent', '--startup-approval-stdin'], { npm_config_agent: 'false' })).toEqual(['--only=agent', '--startup-approval-stdin']);
  });

  it('recovers the messaging opt-out from both npm negative flag spellings', () => {
    expect(normalizeLauncherArgs([], { npm_config_imessage: 'false' })).toEqual(['--no-imessage']);
    expect(normalizeLauncherArgs([], { npm_config_imessage: '' })).toEqual(['--no-imessage']);
    expect(normalizeLauncherArgs([], { npm_config_no_imessage: 'true' })).toEqual(['--no-imessage']);
    expect(normalizeLauncherArgs([], { npm_config_no_imessage: 'false' })).toEqual([]);
  });

  it('explicit messaging opt-in overrides an inherited npm opt-out', () => {
    expect(normalizeLauncherArgs(['--imessage'], { npm_config_no_imessage: 'true' })).toEqual(['--imessage']);
    expect(normalizeLauncherArgs(['--imessage'], { npm_config_imessage: 'false' })).toEqual(['--imessage']);
  });

  it('explicit messaging opt-out overrides an inherited npm opt-in', () => {
    expect(normalizeLauncherArgs(['--no-imessage'], { npm_config_imessage: 'true' })).toEqual(['--no-imessage']);
  });

  it('conflicting inherited messaging options resolve to one opt-out without changing inputs', () => {
    const args = ['--local-only'];
    const environment = { npm_config_imessage: 'true', npm_config_no_imessage: 'true' };
    expect(normalizeLauncherArgs(args, environment)).toEqual(['--local-only', '--no-imessage']);
    expect(args).toEqual(['--local-only']);
    expect(environment).toEqual({ npm_config_imessage: 'true', npm_config_no_imessage: 'true' });
  });

  it('retains unknown flags for Python to reject and ignores boolean-looking string values', () => {
    expect(normalizeLauncherArgs(['--unknown'], { npm_config_studio: 'true', npm_config_only: 'false' })).toEqual(['--unknown']);
  });
});

describe('private process markers and prompt forwarding', () => {
  it('accepts only known process names and valid operating-system PIDs', () => {
    expect(parseProcessMarker('[janus-process] agent 123')).toEqual({ name: 'agent', pid: 123 });
    expect(parseProcessMarker('[janus-process] tunnel-backend 456')).toEqual({ name: 'tunnel-backend', pid: 456 });
    for (const marker of ['[janus-process] unknown 123', '[janus-process] agent 0', '[janus-process] agent -1',
      '[janus-process] agent 2147483648', 'tool [janus-process] agent 123', '[janus-process] agent 123 extra']) {
      expect(parseProcessMarker(marker)).toBeNull();
    }
  });

  it('forwards a permission prompt before any newline arrives', () => {
    const writes = [];
    const relay = createOutputRelay((value) => writes.push(value), () => { throw new Error('no process expected'); });
    relay.push('Type yes to approve: ');
    expect(writes.join('')).toBe('Type yes to approve: ');
  });

  it('consumes split protocol markers without displaying them', () => {
    const writes = [];
    const processes = [];
    const relay = createOutputRelay((value) => writes.push(value), (value) => processes.push(value));
    relay.push('Studio ready\n[janus-');
    relay.push('process] agent 123\r\n');
    relay.push('[janus-process] pwa 456\n');
    relay.flush();
    expect(writes.join('')).toBe('Studio ready\n');
    expect(processes).toEqual([{ name: 'agent', pid: 123 }, { name: 'pwa', pid: 456 }]);
  });

  it('never interprets a marker inside service output as a new process', () => {
    const writes = [];
    const processes = [];
    const relay = createOutputRelay((value) => writes.push(value), (value) => processes.push(value));
    relay.push('[agent] logged ');
    relay.push('[janus-process] agent 999\n');
    relay.push('[janus-process] unexpected 999\n');
    relay.push('final line without newline');
    relay.flush();
    expect(processes).toEqual([]);
    expect(writes.join('')).toBe('[agent] logged [janus-process] agent 999\n[janus-process] unexpected 999\nfinal line without newline');
  });
});

describe('fallback process cleanup ownership', () => {
  it('excludes dead or recycled PIDs and records replaced by another launcher', () => {
    const records = {
      agent: { pid: 10, owner: 1, startedAt: 100 },
      backend: { pid: 11, owner: 1, startedAt: 100 },
      pwa: { pid: 12, owner: 2, startedAt: 100 },
      unrelated: { pid: 13, owner: 1, startedAt: 100 },
    };
    expect(ownedRecordedProcesses(['agent', 'backend', 'pwa', 'absent'], {
      owner: 1, read: () => records, recorded: (record) => record.pid !== 11,
    })).toEqual([{ name: 'agent', ...records.agent }]);
  });
});

describe('real wrapper process orchestration', () => {
  const waitUntil = async (condition, timeout = 6000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (condition()) return true;
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    return condition();
  };

  async function fixture(finish, exercise) {
    const directory = mkdtempSync(path.join(tmpdir(), 'janus-wrapper-'));
    const fakePython = `
      console.log('PYTHONUNBUFFERED=' + process.env.PYTHONUNBUFFERED);
      console.log('[janus-process] agent ' + process.pid);
      process.stdout.write('Approve fixture startup: ');
      let input = '';
      process.stdin.setEncoding('utf8');
      process.stdin.on('data', chunk => {
        input += chunk;
        if (input.includes('\\n')) {
          console.log(input === 'JANUS_STARTUP_APPROVED\\n' ? 'APPROVED' : 'DENIED');
          if (process.env.FAKE_FINISH === '1') process.exit(0);
        }
      });
      process.on('SIGTERM', () => { console.log('FAKE_CHILD_STOPPED'); process.exit(0); });
      setInterval(() => {}, 1000);
    `;
    const fakeUv = `#!/usr/bin/env node
      const { spawn } = require('node:child_process');
      const child = spawn(process.execPath, ['-e', ${JSON.stringify(fakePython)}], { stdio: 'inherit' });
      child.on('exit', code => process.exit(code ?? 0));
      // Deliberately does not forward signals: the wrapper must signal the group.
      process.on('SIGTERM', () => { console.log('FAKE_UV_STOPPED'); process.exit(0); });
      setInterval(() => {}, 1000);
    `;
    writeFileSync(path.join(directory, 'uv'), fakeUv, { mode: 0o700 });
    const script = fileURLToPath(new URL('../start.mjs', import.meta.url));
    const run = spawn(process.execPath, [script, '--only=agent', '--startup-approval-stdin'], {
      env: { ...process.env, PATH: `${directory}${path.delimiter}${process.env.PATH ?? ''}`, JANUS_STATE_DIR: directory,
        PYTHONUNBUFFERED: '0', FAKE_FINISH: finish ? '1' : '0' },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let output = '';
    run.stdout.on('data', (chunk) => { output += chunk; });
    run.stderr.on('data', (chunk) => { output += chunk; });
    const completion = new Promise((resolve, reject) => {
      run.once('error', reject);
      run.once('close', (code, signal) => resolve({ code, signal, output }));
    });
    try {
      expect(await waitUntil(() => output.includes('Approve fixture startup: '))).toBe(true);
      const pidFile = path.join(directory, 'pids.json');
      expect(await waitUntil(() => existsSync(pidFile) && JSON.parse(readFileSync(pidFile, 'utf8'))['agent-solo'])).toBe(true);
      const records = JSON.parse(readFileSync(pidFile, 'utf8'));
      await exercise({ run, directory, records, completion });
    } finally {
      if (run.exitCode === null && run.signalCode === null) run.kill('SIGTERM');
      await completion;
      rmSync(directory, { recursive: true, force: true });
    }
  }

  it.skipIf(process.platform === 'win32')('records the actual solo agent PID privately and sends consent through inherited stdin', async () => {
    await fixture(true, async ({ run, directory, records, completion }) => {
      expect(records['agent-solo'].owner).toBe(run.pid);
      expect(records['launcher-solo'].owner).toBe(run.pid);
      expect(records['agent-solo'].pid).not.toBe(records['launcher-solo'].pid);
      run.stdin.end('JANUS_STARTUP_APPROVED\n');
      const result = await completion;
      expect(result.code).toBe(0);
      expect(result.output).toContain('PYTHONUNBUFFERED=1');
      expect(result.output).toContain('APPROVED');
      expect(result.output).not.toContain('[janus-process]');
      expect(existsSync(path.join(directory, 'pids.json'))).toBe(false);
    });
  }, 15000);

  it.skipIf(process.platform === 'win32')('SIGINT stops uv and its child without waiting for the force-cleanup timeout', async () => {
    await fixture(false, async ({ run, directory, records, completion }) => {
      const stoppedAt = Date.now();
      run.kill('SIGINT');
      expect(await waitUntil(() => run.exitCode !== null, 5000)).toBe(true);
      const result = await completion;
      expect(result.code).toBe(0);
      expect(Date.now() - stoppedAt).toBeLessThan(5000);
      expect(result.output).toContain('FAKE_CHILD_STOPPED');
      expect(result.output).toContain('FAKE_UV_STOPPED');
      expect(isAlive(records['agent-solo'].pid)).toBe(false);
      expect(existsSync(path.join(directory, 'pids.json'))).toBe(false);
    });
  }, 15000);
});
