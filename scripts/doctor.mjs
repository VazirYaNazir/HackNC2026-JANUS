// Preflight checks. `setup` and `start` run these automatically and print only failures;
// `npm run doctor` runs them directly and confirms when everything passes.
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ENV_FILE, ROOT, capture, isJanusProcess, isWindows, listenerPid, makeFlags, portInUse, processName, readDevices, readEnvFile, which } from './lib/common.mjs';
import { findCloudflared } from './lib/cloudflared.mjs';

const MIN_NODE = [20, 19];

function nodeCheck() {
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major > MIN_NODE[0] || (major === MIN_NODE[0] && minor >= MIN_NODE[1])) return null;
  return { level: 'error', name: 'Node.js', message: `Node ${process.versions.node} is too old (need ${MIN_NODE.join('.')}+).`, fix: 'Install a current Node LTS from https://nodejs.org.' };
}

function uvCheck() {
  if (which('uv')) return null;
  return { level: 'error', name: 'uv', message: 'uv (the Python package manager) was not found on PATH.', fix: 'Install it from https://docs.astral.sh/uv/getting-started/installation/ — uv fetches Python 3.12+ itself.' };
}

function compilerCheck() {
  if (['cl', 'gcc', 'clang', 'zig'].some(which)) return null;
  const toolchains = isWindows ? path.join(process.env.LOCALAPPDATA ?? '', 'HackNC2026', 'toolchains') : path.join(process.env.HOME ?? '', '.cache', 'HackNC2026', 'toolchains');
  if (existsSync(toolchains)) return null;
  return { level: 'warn', name: 'C compiler', message: 'No C compiler found.', fix: 'The first native-tools build downloads a checksum-verified Zig into your user profile (a few minutes). Or install MSVC/MinGW/Xcode tools.' };
}

function envCheck() {
  if (!existsSync(ENV_FILE)) return { level: 'warn', name: 'backend/.env', message: 'backend/.env does not exist yet.', fix: 'Run `npm run setup`.' };
  const devices = readDevices();
  const env = readEnvFile();
  if (env?.get('AGENT_DEVICES_FILE')) return null;
  if (!devices?.length) return { level: 'error', name: 'backend/.env', message: 'AGENT_DEVICES_JSON is missing, invalid, or empty — no laptop is registered.', fix: 'Run `npm run setup` (it registers this laptop without touching existing values).' };
  const weak = devices.find((d) => String(d.agent_token ?? '').length < 32 || String(d.controller_token ?? '').length < 32);
  if (weak) return { level: 'error', name: 'backend/.env', message: `Device "${weak.device_id}" has a credential shorter than 32 characters.`, fix: 'Generate longer random tokens (see backend/app/docs/backend-agent.md).' };
  return null;
}

async function portCheck(port, label) {
  if (!(await portInUse(port))) return null;
  const pid = listenerPid(port);
  if (pid && isJanusProcess(pid)) return { level: 'warn', name: `port ${port}`, message: `${label} port ${port} is held by a leftover JANUS process (PID ${pid}); \`npm start\` will stop it.`, fix: '' };
  const who = pid ? `${processName(pid)} (PID ${pid})` : 'an unknown process';
  return { level: 'error', name: `port ${port}`, message: `${label} port ${port} is already in use by ${who}.`, fix: 'Close that program, or stop it yourself if you recognise it. JANUS never kills processes it did not start.' };
}

/**
 * AI widgets need a Gemini key (backend) and a local isolation adapter (agent). Neither is required for the
 * built-in native controls, so both are warnings, never blockers.
 */
export function aiChecks() {
  const env = readEnvFile();
  const key = process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY || env?.get('GEMINI_API_KEY') || env?.get('GOOGLE_API_KEY');
  const results = [];
  if (!key) {
    results.push({ level: 'warn', name: 'AI widgets · Gemini key', message: 'No Gemini key found, so AI-generated widgets are off. Built-in controls (volume, mouse, metrics, processes, screen) work without it.', fix: 'Run `npm run setup` to enter one, or set GEMINI_API_KEY in backend/.env.' });
  }
  if (!process.env.AGENT_ISOLATION_ADAPTER) {
    results.push({ level: 'warn', name: 'AI widgets · isolation adapter', message: 'No isolation adapter is configured (AGENT_ISOLATION_ADAPTER), so this laptop cannot run AI-generated programs. JANUS does not ship one yet. Built-in controls are unaffected.', fix: 'See README → AI widgets → Isolation adapter.' });
  }
  return results;
}

/** Turns the JSON that `python -m app.tiger.check` prints into doctor results. Tiger is optional, so these only ever warn. */
export function interpretTigerCheck(output) {
  let report;
  try { report = JSON.parse(output.trim().split('\n').pop() ?? ''); } catch {
    return [{ level: 'warn', name: 'Tiger Data', message: 'Could not check Tiger Data (the check produced no readable result).', fix: 'Run `uv run --locked python -m app.tiger.check` in backend/ to see why.' }];
  }
  if (!report.configured) return [];
  if (!report.reachable) {
    return [{ level: 'warn', name: 'Tiger Data · connection', message: `TIGER_DATABASE_URL is set but the database is not reachable (${report.error ?? 'unknown error'}). Samples are dropped until it is, and history features report "unavailable".`, fix: 'Check the URL, that the service is running, and your network (IPv4 is preferred automatically).' }];
  }
  if (!report.schema) {
    return [{ level: 'warn', name: 'Tiger Data · schema', message: `Connected to Tiger Data, but the schema is incomplete (missing: ${(report.missing ?? []).join(', ') || 'unknown'}).`, fix: 'Run `npm run setup` (or `uv run --locked python -m app.tiger.migrate` in backend/) to create it.' }];
  }
  return [];
}

/** Only runs when TIGER_DATABASE_URL is set (in the environment or backend/.env); otherwise Tiger Data is simply off. */
export function tigerChecks(env = readEnvFile()) {
  const url = (process.env.TIGER_DATABASE_URL || env?.get('TIGER_DATABASE_URL') || '').trim();
  if (!url) return [];
  const run = capture('uv', ['run', '--locked', 'python', '-m', 'app.tiger.check'], { cwd: path.join(ROOT, 'backend'), env: { ...process.env, TIGER_DATABASE_URL: url }, timeout: 45_000 });
  if (run.code !== 0 && !run.stdout.trim()) {
    return [{ level: 'warn', name: 'Tiger Data', message: 'Could not check Tiger Data (the backend check did not run).', fix: 'Run `npm run setup` so the backend dependencies are installed.' }];
  }
  return interpretTigerCheck(run.stdout);
}

const E164 = /^\+[1-9]\d{6,14}$/;

/** Warnings for `npm start -- --imessage`: the channel does nothing without credentials and an allowlist. */
export function imessageChecks(env = readEnvFile()) {
  // Example/placeholder values ("...", "+1XXXXXXXXXX") count as not configured.
  const value = (key) => { const raw = (process.env[key] || env?.get(key) || '').trim(); return /^\.+$|^\+?1?X+$/i.test(raw) ? '' : raw; };
  const results = [];
  if (!value('SPECTRUM_PROJECT_ID') || !value('SPECTRUM_PROJECT_SECRET')) {
    results.push({ level: 'warn', name: 'iMessage · Spectrum credentials', message: 'SPECTRUM_PROJECT_ID / SPECTRUM_PROJECT_SECRET are not set in backend/.env, so the iMessage sidecar will not start.', fix: 'Create a Photon Spectrum project and add both values to backend/.env.' });
  }
  const senders = value('IMESSAGE_ALLOWED_SENDERS').split(',').map((entry) => entry.replace(/[\s().-]/g, '')).filter((entry) => E164.test(entry));
  if (!senders.length) {
    results.push({ level: 'warn', name: 'iMessage · allowlist', message: 'IMESSAGE_ALLOWED_SENDERS has no valid E.164 number (e.g. +15551234567), so every text will be ignored.', fix: 'Add your number to IMESSAGE_ALLOWED_SENDERS in backend/.env.' });
  }
  if (value('MESSAGING_SERVICE_TOKEN').length < 32) {
    results.push({ level: 'warn', name: 'iMessage · service token', message: 'MESSAGING_SERVICE_TOKEN is missing or too short, so the backend will refuse the sidecar.', fix: 'Run `npm run setup` to generate one.' });
  }
  return results;
}

/** @param {{ tunnel?: boolean, pwa?: boolean, ports?: boolean, beforeSetup?: boolean, ai?: boolean, imessage?: boolean, tiger?: boolean }} needs */
export async function doctor(needs = {}) {
  const results = [nodeCheck(), uvCheck(), compilerCheck()];
  if (!needs.beforeSetup) results.push(envCheck());
  if (needs.tunnel && !findCloudflared()) {
    results.push({ level: 'warn', name: 'cloudflared', message: 'cloudflared is not installed (needed for --tunnel).', fix: '`npm start -- --tunnel` downloads a pinned, checksum-verified copy into .janus/bin.' });
  }
  if (needs.ports) {
    results.push(await portCheck(8000, 'Backend'), await portCheck(5173, 'Studio'));
    if (needs.pwa) results.push(await portCheck(5174, 'PWA dev'));
  }
  if (needs.ai) results.push(...aiChecks());
  if (needs.imessage) results.push(...imessageChecks());
  if (needs.tiger) results.push(...tigerChecks());
  return results.filter(Boolean);
}

/** Prints failures only. Returns true when nothing blocking was found. */
export function report(results) {
  for (const result of results) {
    console.error(`${result.level === 'error' ? '✗' : '!'} ${result.name}: ${result.message}${result.fix ? `\n    → ${result.fix}` : ''}`);
  }
  return !results.some((result) => result.level === 'error');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const flags = makeFlags();
  const results = await doctor({ tunnel: flags.has('tunnel'), pwa: flags.has('pwa'), ports: true, ai: true, tiger: true, imessage: flags.has('imessage') });
  const ok = report(results);
  if (ok && results.length === 0) console.log('All checks passed.');
  process.exit(ok ? 0 : 1);
}
