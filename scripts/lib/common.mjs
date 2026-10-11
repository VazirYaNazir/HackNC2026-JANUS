// Shared helpers for the setup / doctor / start scripts. Node built-ins only, so they run before `npm ci`.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = fileURLToPath(new URL('../..', import.meta.url));
// JANUS_STATE_DIR overrides the state directory (pidfile, pinned cloudflared, iMessage state); tests rely on it.
export const JANUS_DIR = process.env.JANUS_STATE_DIR || path.join(ROOT, '.janus');
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

// ---- pidfile: lets `npm start` recognise (and only ever stop) its own earlier processes ---------------------------

const PID_FILE = path.join(JANUS_DIR, 'pids.json');
/** A recorded PID only counts as ours if the live process started within this window of when we recorded it. */
const START_TOLERANCE_MS = 30_000;

/**
 * {name: {pid, startedAt, owner, ownerStartedAt}}: the child, and the start.mjs process that launched it.
 * Also reads the older {name: pid} shape (startedAt 0 = cannot be verified).
 */
export function readRecords() {
  let raw;
  try { raw = JSON.parse(readFileSync(PID_FILE, 'utf8')); } catch { return {}; }
  const records = {};
  for (const [name, value] of Object.entries(raw && typeof raw === 'object' ? raw : {})) {
    if (typeof value === 'number') records[name] = { pid: value, startedAt: 0, owner: 0, ownerStartedAt: 0 };
    else if (value && typeof value.pid === 'number') {
      records[name] = { pid: value.pid, startedAt: Number(value.startedAt) || 0, owner: Number(value.owner) || 0, ownerStartedAt: Number(value.ownerStartedAt) || 0 };
    }
  }
  return records;
}

export function writeRecords(records) {
  mkdirSync(JANUS_DIR, { recursive: true });
  if (Object.keys(records).length === 0) rmSync(PID_FILE, { force: true });
  else writeFileSync(PID_FILE, JSON.stringify(records));
}

/** name -> pid. */
export function readPids() {
  return Object.fromEntries(Object.entries(readRecords()).map(([name, record]) => [name, record.pid]));
}

export function recordProcess(name, pid, startedAt = Date.now(), owner = process.pid, ownerStartedAt = Math.round(Date.now() - process.uptime() * 1000)) {
  writeRecords({ ...readRecords(), [name]: { pid, startedAt, owner, ownerStartedAt } });
}

export function forgetProcesses(names) {
  const records = readRecords();
  for (const name of names) delete records[name];
  writeRecords(records);
}

/** Process creation time in ms since the epoch, or null when it cannot be determined. */
export function processCreatedAt(pid) {
  const out = isWindows
    ? capture('powershell', ['-NoProfile', '-Command', `(Get-Process -Id ${Number(pid)} -ErrorAction Stop).StartTime.ToUniversalTime().ToString('o')`]).stdout
    : capture('ps', ['-o', 'lstart=', '-p', String(pid)]).stdout;
  const time = Date.parse(out.trim());
  return Number.isFinite(time) ? time : null;
}

function startedNear(pid, expected, alive, createdAt) {
  if (!expected || !alive(pid)) return false;
  const created = createdAt(pid);
  return created !== null && Math.abs(created - expected) <= START_TOLERANCE_MS;
}

/** True only when `pid` is alive AND started when we recorded it, so a recycled PID is never mistaken for ours. */
export function isRecordedProcess(record, { alive = isAlive, createdAt = processCreatedAt } = {}) {
  return Boolean(record) && startedNear(record.pid, record.startedAt, alive, createdAt);
}

/**
 * A start.mjs that is still running and owns recorded processes (other than this one). A second `npm start` must not
 * kill a healthy stack as "leftovers": it refuses instead. Solo agents (from Studio's button) are excluded; a full
 * start replaces those.
 */
export function otherOrchestrator({ alive = isAlive, createdAt = processCreatedAt, self = process.pid } = {}) {
  for (const [name, record] of Object.entries(readRecords())) {
    if (name.endsWith('-solo') || !record.owner || record.owner === self) continue;
    if (startedNear(record.owner, record.ownerStartedAt, alive, createdAt)) return { name, ...record };
  }
  return null;
}

/**
 * Kills every process tree recorded by an earlier run (a crash or a hard kill can leave them behind) and forgets the
 * records. Entries whose PID is dead, or was recycled by an unrelated process, are dropped without touching anything.
 * Returns one line per entry for the log.
 */
export function killLeftovers({ keep = [], alive = isAlive, createdAt = processCreatedAt, kill = killTree } = {}) {
  const lines = [];
  const records = readRecords();
  const done = [];
  for (const [name, record] of Object.entries(records)) {
    if (keep.includes(name)) continue;
    done.push(name);
    if (!alive(record.pid)) continue;
    if (isRecordedProcess(record, { alive, createdAt })) {
      kill(record.pid);
      lines.push(`Stopped leftover ${name} from an earlier run (PID ${record.pid}).`);
    } else {
      lines.push(`Left PID ${record.pid} (${name}) alone: it is running but cannot be confirmed as a JANUS process.`);
    }
  }
  forgetProcesses(done);
  return lines;
}

/** The recorded agent (from `npm start` or Studio's Start agent button) if it is still genuinely running. */
export function liveAgent(deps) {
  for (const [name, record] of Object.entries(readRecords())) {
    if ((name === 'agent' || name === 'agent-solo') && isRecordedProcess(record, deps)) return { name, ...record };
  }
  return null;
}

/** True only when `pid` is, or descends from, a live process recorded by an earlier JANUS start. */
export function isJanusProcess(pid) {
  const recorded = new Set(Object.values(readPids()).filter(isAlive));
  let current = pid;
  for (let depth = 0; current && depth < 8; depth += 1) {
    if (recorded.has(current)) return true;
    current = parentPid(current);
  }
  return false;
}

// ---- flags ---------------------------------------------------------------------------------------------------

/**
 * Command-line flags that also survive npm swallowing them. Windows PowerShell drops the `--` separator, so
 * `npm start -- --imessage` reaches npm as its own option and the script only sees `npm_config_imessage=true`
 * (and `--no-agent` arrives as `npm_config_agent` set to false / empty). Both spellings work here.
 */
export function makeFlags(argv = process.argv.slice(2), env = process.env) {
  const fromEnv = (name) => env[`npm_config_${name.replace(/-/g, '_')}`];
  return {
    has(name) {
      if (argv.includes(`--${name}`)) return true;
      if (name.startsWith('no-')) {
        const negated = fromEnv(name.slice(3));
        return negated === '' || negated === 'false';
      }
      return fromEnv(name) === 'true';
    },
    value(name) {
      const inline = argv.find((arg) => arg.startsWith(`--${name}=`));
      if (inline) return inline.split('=').slice(1).join('=');
      const viaNpm = fromEnv(name);
      return viaNpm && viaNpm !== 'true' && viaNpm !== 'false' ? viaNpm : undefined;
    },
  };
}
