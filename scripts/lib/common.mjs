// Shared helpers for the setup / doctor / start scripts. Node built-ins only, so they run before `npm ci`.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = fileURLToPath(new URL('../..', import.meta.url));
export const JANUS_DIR = path.join(ROOT, '.janus');
export const ENV_FILE = path.join(ROOT, 'backend', '.env');
export const ENV_EXAMPLE = path.join(ROOT, 'backend', '.env.example');
export const isWindows = process.platform === 'win32';

export function run(command, args, { cwd = ROOT, env = process.env } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd, env, stdio: 'inherit', shell: isWindows });
    child.on('error', reject);
    child.on('exit', (code) => (code === 0 ? resolve() : reject(new Error(`${command} ${args.join(' ')} failed (${code})`))));
  });
}

export function capture(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', shell: false, ...options });
  return { code: result.status ?? 1, stdout: result.stdout ?? '', stderr: result.stderr ?? '', error: result.error };
}

/** Absolute path of an executable on PATH, or null. */
export function which(name) {
  const result = capture(isWindows ? 'where.exe' : 'which', [name]);
  return result.code === 0 ? result.stdout.split(/\r?\n/)[0].trim() || null : null;
}

// ---- .env parsing -------------------------------------------------------------------------------------------

export function parseEnv(text) {
  const values = new Map();
  for (const line of text.split(/\r?\n/)) {
    const match = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line);
    if (match) values.set(match[1], match[2]);
  }
  return values;
}

export function readEnvFile() {
  return existsSync(ENV_FILE) ? parseEnv(readFileSync(ENV_FILE, 'utf8')) : null;
}

/** Registered devices from backend/.env (AGENT_DEVICES_JSON, or null when absent/invalid). */
export function readDevices() {
  const env = readEnvFile();
  if (!env) return null;
  try {
    const devices = JSON.parse(env.get('AGENT_DEVICES_JSON') || '[]');
    return Array.isArray(devices) ? devices : null;
  } catch {
    return null;
  }
}

/** Owner-only permissions: chmod 600 on POSIX, an ACL limited to the current user on Windows. */
export function restrictToOwner(file) {
  if (!isWindows) {
    try { return spawnSync('chmod', ['600', file]).status === 0; } catch { return false; }
  }
  const user = process.env.USERNAME;
  if (!user) return false;
  const result = capture('icacls', [file, '/inheritance:r', '/grant:r', `${user}:(F)`]);
  return result.code === 0;
}

// ---- ports and processes ------------------------------------------------------------------------------------

export function portInUse(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ port, host: '127.0.0.1' });
    const finish = (used) => { socket.destroy(); resolve(used); };
    socket.setTimeout(500, () => finish(false));
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
  });
}

/** PID listening on `port`, or null (best effort). */
export function listenerPid(port) {
  if (isWindows) {
    const out = capture('netstat', ['-ano', '-p', 'tcp']).stdout;
    for (const line of out.split(/\r?\n/)) {
      const cols = line.trim().split(/\s+/);
      if (cols[3] === 'LISTENING' && cols[1].endsWith(`:${port}`)) return Number(cols[4]);
    }
    return null;
  }
  const pid = capture('lsof', ['-nP', `-iTCP:${port}`, '-sTCP:LISTEN', '-t']).stdout.split(/\s+/)[0];
  return pid ? Number(pid) : null;
}

export function processName(pid) {
  if (isWindows) {
    const out = capture('tasklist', ['/FI', `PID eq ${pid}`, '/FO', 'CSV', '/NH']).stdout.trim();
    return out.startsWith('"') ? out.split('","')[0].replace(/^"/, '') : 'unknown';
  }
  return capture('ps', ['-p', String(pid), '-o', 'comm=']).stdout.trim() || 'unknown';
}

function parentPid(pid) {
  if (isWindows) {
    const out = capture('powershell', ['-NoProfile', '-Command', `(Get-CimInstance Win32_Process -Filter "ProcessId=${Number(pid)}").ParentProcessId`]).stdout.trim();
    return Number(out) || null;
  }
  return Number(capture('ps', ['-p', String(pid), '-o', 'ppid=']).stdout.trim()) || null;
}

export function isAlive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

export function killTree(pid) {
  if (isWindows) capture('taskkill', ['/PID', String(pid), '/T', '/F']);
  else { try { process.kill(-pid, 'SIGTERM'); } catch { try { process.kill(pid, 'SIGTERM'); } catch { /* already gone */ } } }
}

// ---- pidfile: lets `npm start` recognise (and only ever stop) its own earlier processes -----------------------

const PID_FILE = path.join(JANUS_DIR, 'pids.json');

export function readPids() {
  try { return JSON.parse(readFileSync(PID_FILE, 'utf8')); } catch { return {}; }
}

export function writePids(pids) {
  mkdirSync(JANUS_DIR, { recursive: true });
  if (Object.keys(pids).length === 0) rmSync(PID_FILE, { force: true });
  else writeFileSync(PID_FILE, JSON.stringify(pids));
}

/** True only when `pid` is, or descends from, a live process recorded by an earlier Janus start. */
export function isJanusProcess(pid) {
  const recorded = new Set(Object.values(readPids()).filter(isAlive));
  let current = pid;
  for (let depth = 0; current && depth < 8; depth += 1) {
    if (recorded.has(current)) return true;
    current = parentPid(current);
  }
  return false;
}
