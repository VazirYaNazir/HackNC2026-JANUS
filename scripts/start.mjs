// One startup path: Python owns readiness, permissions, native tools and phone tunnels.
// This Node entrypoint preserves npm/PowerShell flags and Electron's PID status bridge.
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ROOT, forgetProcesses, isRecordedProcess, isWindows, killLeftovers, killTree,
  liveAgent, makeFlags, otherOrchestrator, readRecords, recordProcess,
} from './lib/common.mjs';

const BOOLEAN_FLAGS = ['pwa', 'tunnel', 'screen', 'imessage', 'no-imessage', 'no-agent', 'local-only', 'no-browser', 'no-install', 'electron', 'no-screen', 'startup-approval-stdin'];
const VALUE_FLAGS = ['only', 'studio', 'device-id', 'backend-port', 'studio-port', 'pwa-port', 'admin-port', 'cloudflared'];
const PROCESS_NAMES = new Set(['backend', 'studio', 'pwa', 'agent', 'imessage', 'electron', 'tunnel-backend', 'tunnel-pwa']);
const MARKER = '[janus-process] ';

/** Recover flags npm consumed without overriding explicit CLI values or opposites. */
export function normalizeLauncherArgs(argv = [], env = {}) {
  const args = argv.filter((value) => value !== '--');
  const flags = makeFlags(args, env);
  const explicit = (name) => args.some((value) => value === `--${name}` || value.startsWith(`--${name}=`));
  const opposite = (name) => name.startsWith('no-') ? name.slice(3) : `no-${name}`;
  for (const name of BOOLEAN_FLAGS) {
    if (name === 'electron' && explicit('studio')) continue;
    if (name === 'local-only' && explicit('tunnel') || name === 'tunnel' && explicit('local-only')) continue;
    if (name === 'no-agent' && explicit('only')) continue;
    // npm can preserve --no-imessage either as imessage=false or no_imessage=true.
    // An inherited opt-out wins over inherited opt-in; explicit CLI still wins above both.
    if (name === 'imessage' && env.npm_config_no_imessage === 'true') continue;
    const inherited = flags.has(name) || name === 'no-imessage' && env.npm_config_no_imessage === 'true';
    if (!explicit(name) && !explicit(opposite(name)) && inherited) args.push(`--${name}`);
  }
  for (const name of VALUE_FLAGS) {
    if (explicit(name) || name === 'studio' && explicit('electron')) continue;
    const value = flags.value(name);
    if (value !== undefined) args.push(`--${name}=${value}`);
  }
  return args;
}

export function launcherCommand(args) {
  return ['uv', ['run', '--locked', '--project', path.join(ROOT, 'backend'), 'python', path.join(ROOT, 'start.py'), ...args]];
}

export function parseProcessMarker(line) {
  const match = /^\[janus-process\] ([a-z][a-z0-9-]*) ([1-9]\d*)$/.exec(line);
  if (!match || !PROCESS_NAMES.has(match[1])) return null;
  const pid = Number(match[2]);
  return Number.isSafeInteger(pid) && pid <= 2_147_483_647 ? { name: match[1], pid } : null;
}

/** Forward prompts immediately; consume process markers only at a line boundary. */
export function createOutputRelay(write, onProcess) {
  let pending = '';
  let atLineStart = true;
  const line = (value) => {
    const marker = atLineStart ? parseProcessMarker(value.replace(/\r$/, '')) : null;
    if (marker) onProcess(marker);
    else write(value + '\n');
    atLineStart = true;
  };
  return {
    push(chunk) {
      pending += chunk.toString();
      let newline;
      while ((newline = pending.indexOf('\n')) !== -1) {
        line(pending.slice(0, newline));
        pending = pending.slice(newline + 1);
      }
      if (pending && (!atLineStart || !(MARKER.startsWith(pending) || pending.startsWith(MARKER)))) {
        write(pending);
        pending = '';
        atLineStart = false;
      }
    },
    flush() {
      if (pending) write(pending);
      pending = '';
    },
  };
}

/** Recheck both owner and creation time before fallback cleanup touches a PID. */
export function ownedRecordedProcesses(keys, { owner = process.pid, read = readRecords, recorded = isRecordedProcess } = {}) {
  const records = read();
  return keys.flatMap((name) => {
    const record = records[name];
    return record && record.owner === owner && recorded(record) ? [{ name, ...record }] : [];
  });
}

function optionValue(args, name) {
  const inline = args.find((arg) => arg.startsWith(`--${name}=`));
  if (inline) return inline.slice(name.length + 3);
  const index = args.indexOf(`--${name}`);
  return index >= 0 ? args[index + 1] : undefined;
}

export async function main(argv = process.argv.slice(2), env = process.env) {
  const args = normalizeLauncherArgs(argv, env);
  const solo = optionValue(args, 'only') === 'agent';
  const help = args.includes('--help') || args.includes('-h');
  if (!help) {
    if (solo) {
      const existing = liveAgent();
      if (existing) {
        console.error(`An agent is already running (PID ${existing.pid}); not starting a second one. Stop it first if you want to replace it.`);
        return 1;
      }
    } else {
      const existing = otherOrchestrator();
      if (existing) {
        console.error(`JANUS is already running (started by PID ${existing.owner}; ${existing.name} is PID ${existing.pid}). Stop it first (Ctrl+C in its window), then start again.`);
        return 1;
      }
      for (const message of killLeftovers()) console.log(message);
    }
  }

  const [command, commandArgs] = launcherCommand(args);
  const keys = new Set();
  const child = spawn(command, commandArgs, {
    cwd: ROOT, env: { ...env, PYTHONUNBUFFERED: '1' }, shell: false, detached: !isWindows,
    // Electron supplies a pipe only after its own permission dialog approves.
    // In a terminal this preserves isatty() and the Python startup prompt.
    stdio: ['inherit', 'pipe', 'pipe'], windowsHide: true,
  });
  const track = ({ name, pid }) => {
    const key = solo ? `${name}-solo` : name;
    keys.add(key);
    recordProcess(key, pid);
  };
  if (child.pid && !help) {
    const key = solo ? 'launcher-solo' : 'launcher';
    keys.add(key);
    recordProcess(key, child.pid);
  }
  const stdout = createOutputRelay((value) => process.stdout.write(value), track);
  const stderr = createOutputRelay((value) => process.stderr.write(value), track);
  child.stdout.on('data', (chunk) => stdout.push(chunk));
  child.stderr.on('data', (chunk) => stderr.push(chunk));

  let stopping = false;
  let fallbackTimer;
  const cleanup = () => {
    if (fallbackTimer) clearTimeout(fallbackTimer);
    // The Python launcher normally closes everything first. Recycled PIDs and
    // records another run replaced are excluded from this final safety net.
    const owned = ownedRecordedProcesses([...keys]);
    for (const record of owned.reverse()) {
      killTree(record.pid);
      if (!isWindows && isRecordedProcess(record)) {
        try { process.kill(-record.pid, 'SIGKILL'); } catch { /* already stopped */ }
      }
    }
    const current = readRecords();
    forgetProcesses([...keys].filter((name) => current[name]?.owner === process.pid));
  };
  const stop = () => {
    if (stopping) return;
    stopping = true;
    try {
      if (isWindows) killTree(child.pid);
      else process.kill(-child.pid, 'SIGTERM');
    } catch { /* child already exited */ }
    fallbackTimer = setTimeout(() => {
      cleanup();
      if (child.pid) killTree(child.pid);
    }, 8000);
    fallbackTimer.unref();
  };
  const signals = ['SIGINT', 'SIGTERM', 'SIGHUP', ...(isWindows ? ['SIGBREAK'] : [])];
  for (const signal of signals) process.on(signal, stop);
  process.on('exit', cleanup);
  return await new Promise((resolve) => {
    child.once('error', (error) => {
      console.error(`JANUS could not start: ${error.message}`);
      resolve(1);
    });
    child.once('close', (code, signal) => resolve(stopping || signal === 'SIGINT' ? 0 : code ?? 1));
  }).finally(() => {
    stdout.flush(); stderr.flush();
    cleanup();
    for (const signal of signals) process.off(signal, stop);
    process.off('exit', cleanup);
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then((code) => { process.exitCode = code; }).catch((error) => {
    console.error(`JANUS startup failed: ${error.message}`);
    process.exitCode = 1;
  });
}
