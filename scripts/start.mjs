// Starts JANUS: backend, laptop agent and Studio (Electron). Studio's "Start agent" button runs this same
// script with --only=agent, so there is a single orchestration path.
//   npm start [-- --pwa] [-- --tunnel] [-- --studio=electron|browser|none] [-- --no-agent] [-- --only=agent]
//   (Windows PowerShell drops the `--`; both forms work. Shortcuts: npm run start:imessage, npm run start:demo)
//   --pwa     also run the PWA dev server on :5174 (off by default; the phone uses the hosted PWA)
//   --tunnel  expose the backend through a Cloudflare tunnel for phone pairing (off by default)
//   --screen  start the agent with screen sharing for Studio (off by default)
//   --imessage  also run the iMessage sidecar (text your laptop); needs Spectrum credentials + an allowlist in backend/.env
import { spawn } from 'node:child_process';
import path from 'node:path';
import { doctor, report } from './doctor.mjs';
import { ensureCloudflared, BIN_DIR } from './lib/cloudflared.mjs';
import { ROOT, forgetProcesses, isJanusProcess, isWindows, killLeftovers, killTree, listenerPid, liveAgent, makeFlags, otherOrchestrator, portInUse, processName, readDevices, readEnvFile, pathKey, recordProcess, run, tunnelUrlFrom, withIpv4First } from './lib/common.mjs';

const flags = makeFlags();
const only = flags.value('only');
const wantPwa = flags.has('pwa');
const wantTunnel = flags.has('tunnel');
const wantImessage = flags.has('imessage');
const wantScreen = flags.has('screen');
const wantAgent = !flags.has('no-agent');
const studio = flags.value('studio') ?? 'electron';
if (only && only !== 'agent') throw new Error(`Unknown --only=${only}`);
if (!['electron', 'browser', 'none'].includes(studio)) throw new Error(`Unknown --studio=${studio}`);

const BACKEND = 'http://127.0.0.1:8000';
const children = new Map(); // name -> ChildProcess
const pidKeys = new Set(); // keys this run wrote to the pidfile
let shuttingDown = false;

function track(name, child) {
  children.set(name, child);
  const key = only ? `${name}-solo` : name;
  pidKeys.add(key);
  recordProcess(key, child.pid);
}

function launch(name, command, commandArgs, { cwd = ROOT, env = {}, onExit, onLine } = {}) {
  const child = spawn(command, commandArgs, { cwd, env: { ...process.env, ...env }, shell: isWindows, detached: !isWindows, stdio: ['ignore', 'pipe', 'pipe'] });
  const prefix = `[${name}] `;
  for (const stream of [child.stdout, child.stderr]) {
    let pending = '';
    stream.on('data', (chunk) => {
      const lines = (pending + chunk).split(/\r?\n/);
      pending = lines.pop() ?? '';
      for (const line of lines) { console.log(prefix + line); onLine?.(line); }
    });
  }
  child.on('error', (error) => { console.error(`${prefix}failed to start: ${error.message}`); void shutdown(1); });
  child.on('exit', (code) => {
    children.delete(name);
    if (!shuttingDown) {
      console.log(`${prefix}exited (${code})`);
      // Closing Studio, or losing the backend, ends the session; a crashed agent does not.
      if (name === 'studio' || name === 'backend' || only) void shutdown(code ?? 0);
      onExit?.(code);
    }
  });
  track(name, child);
  return child;
}

/** Kills each child's whole process tree (taskkill /T /F on Windows, the process group elsewhere). */
function killChildren() {
  for (const child of children.values()) if (child.pid) killTree(child.pid);
}

async function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  killChildren();
  forgetProcesses([...pidKeys]);
  process.exit(code);
}
// Ctrl+C, a terminated shell, a closed console window, or a crash all tear the whole stack down.
for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP', ...(isWindows ? ['SIGBREAK'] : [])]) process.on(signal, () => void shutdown(0));
process.on('uncaughtException', (error) => { console.error(error); void shutdown(1); });
process.on('unhandledRejection', (error) => { console.error(error); void shutdown(1); });
// Last resort for any other way out: whatever is still tracked is killed, tree and all (synchronously).
process.on('exit', () => { killChildren(); forgetProcesses([...pidKeys]); });

/** Stop leftovers from a previous `npm start`; anything else holding a port aborts with an explanation. */
async function claimPorts(ports) {
  for (const port of ports) {
    if (!(await portInUse(port))) continue;
    const pid = listenerPid(port);
    if (pid && isJanusProcess(pid)) {
      console.log(`Stopping leftover JANUS process on port ${port} (PID ${pid})…`);
      killTree(pid);
      for (let i = 0; i < 20 && (await portInUse(port)); i += 1) await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (await portInUse(port)) {
      const holder = pid ? `${processName(pid)} (PID ${pid})` : 'an unknown process';
      throw new Error(`Port ${port} is in use by ${holder}, which JANUS did not start. Close it and try again.`);
    }
  }
}

async function waitForBackend() {
  for (let i = 0; i < 120; i += 1) {
    try { if ((await fetch(`${BACKEND}/health`)).ok) return; } catch { /* not up yet */ }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('The backend did not become healthy within 60 seconds.');
}

function device() {
  const devices = readDevices();
  const first = devices?.[0];
  if (!first) throw new Error('No laptop is registered in backend/.env. Run `npm run setup` first.');
  if (!/^[A-Za-z0-9._-]+$/.test(first.device_id)) throw new Error('The registered device_id contains unsupported characters.');
  return first;
}

const RED = '\x1b[1;31m';
const GREEN = '\x1b[1;32m';
const RESET = '\x1b[0m';

/** Tells the backend the sidecar is gone so Studio shows "iMessage: off" now rather than after the heartbeat timeout. */
async function markSidecarDown(token) {
  try {
    await fetch(`${BACKEND}/api/messaging/heartbeat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ connected: false }),
      signal: AbortSignal.timeout(2000),
    });
  } catch { /* the backend may be down too; the heartbeat timeout covers that */ }
}

/** The sidecar only forwards texts; the backend decides everything. Secrets go to the child env, never to logs. */
async function startImessage() {
  const env = readEnvFile();
  const value = (key) => process.env[key] || env?.get(key) || '';
  const keys = ['SPECTRUM_PROJECT_ID', 'SPECTRUM_PROJECT_SECRET', 'MESSAGING_SERVICE_TOKEN'];
  const missing = keys.filter((key) => !value(key));
  if (missing.length) { console.warn(`iMessage sidecar not started: set ${missing.join(', ')} in backend/.env.`); return; }
  await run('npm', ['run', 'build', '--workspace', '@janus/imessage']);
  // The allowlist only tells the sidecar whom to greet at startup; the backend still enforces it on every text.
  const optional = ['IMESSAGE_ALLOWED_SENDERS', 'IMESSAGE_GREETING'].filter((key) => value(key));
  launch('imessage', 'node', ['dist/index.js'], {
    cwd: path.join(ROOT, 'imessage'),
    // NODE_OPTIONS keeps whatever the user set and adds IPv4-first DNS; the sidecar also sets it itself on startup.
    env: { ...Object.fromEntries([...keys, ...optional].map((key) => [key, value(key)])), NODE_OPTIONS: withIpv4First() },
    onExit: (code) => {
      console.error(`${RED}iMessage sidecar stopped — texts will not be received${RESET}${code === null ? '' : ` (exit ${code})`}`);
      void markSidecarDown(value('MESSAGING_SERVICE_TOKEN'));
    },
  });
  console.log('iMessage sidecar started (texts from allowlisted numbers only).');
}

async function main() {
  if (only === 'agent') {
    const running = liveAgent();
    if (running) {
      console.error(`An agent is already running (PID ${running.pid}); not starting a second one. Stop it first if you want to replace it.`);
      process.exit(1);
    }
  } else {
    const running = otherOrchestrator();
    if (running) {
      console.error(`JANUS is already running (started by PID ${running.owner}; ${running.name} is PID ${running.pid}). Stop it first (Ctrl+C in its window), then start again.`);
      process.exit(1);
    }
    // A crash or hard kill can leave the previous run's processes behind: stop exactly the ones we recorded.
    for (const line of killLeftovers()) console.log(line);
  }
  const ports = only ? [] : [8000, ...(studio === 'none' ? [] : [5173]), ...(wantPwa ? [5174] : [])];
  const preflight = await doctor({ tunnel: wantTunnel, pwa: wantPwa, ports: false, imessage: wantImessage });
  if (!report(preflight)) process.exit(1);
  await claimPorts(ports);

  const registered = device();
  const agentEnv = { AGENT_TOKEN: registered.agent_token };
  const startAgent = () => launch('agent', 'uv', ['run', '--locked', 'python', '-m', 'agent.main', 'connect', '--url', `ws://127.0.0.1:8000/ws/agents/${registered.device_id}`, '--device-id', registered.device_id, '--native-tools', ...(wantScreen ? ['--screen'] : [])], { cwd: path.join(ROOT, 'agent'), env: agentEnv });

  if (only === 'agent') { await waitForBackend(); startAgent(); return; }

  const backendEnv = {};
  let tunnelUrl = null;
  if (wantTunnel) {
    await ensureCloudflared();
    backendEnv.JANUS_AUTO_TUNNEL = '1';
    // Same key the process already uses ("Path" on Windows), so the pinned cloudflared directory really is first on PATH.
    backendEnv[pathKey()] = `${BIN_DIR}${path.delimiter}${process.env[pathKey()] ?? ''}`;
    console.log('Tunnel enabled: the backend will be reachable on the public internet (still behind its credentials) until you stop JANUS.');
  }
  launch('backend', 'uv', ['run', '--locked', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--env-file', '.env'], {
    cwd: path.join(ROOT, 'backend'),
    env: backendEnv,
    // The backend starts cloudflared during its own startup and logs the public URL; surface it where it can't be missed.
    onLine: (line) => {
      const url = tunnelUrlFrom(line);
      if (url && !tunnelUrl) { tunnelUrl = url; console.log(`${GREEN}Cloudflare tunnel is up: ${url}${RESET}`); }
    },
  });
  await waitForBackend();
  console.log(`Backend ready at ${BACKEND}`);
  if (wantTunnel) {
    // The backend finishes its startup (tunnel included) before /health answers, so a missing URL here means it failed.
    if (tunnelUrl) console.log(`${GREEN}Phone pairing: open Pair Device in Studio — it will use ${tunnelUrl}${RESET}`);
    else console.error(`${RED}The Cloudflare tunnel did not come up, so phone pairing will not work. Look above for "Could not start Cloudflare tunnel" (cloudflared missing or blocked?).${RESET}`);
  }

  if (wantAgent) startAgent();
  if (wantImessage) await startImessage();
  if (wantPwa) launch('pwa', 'npm', ['run', 'dev', '--workspace', '@janus/pwa', '--', '--host', '0.0.0.0', '--port', '5174', '--strictPort']);
  if (studio === 'electron') launch('studio', 'npm', ['run', 'electron', '--workspace', 'frontend']);
  else if (studio === 'browser') { launch('studio', 'npm', ['run', 'dev', '--workspace', 'frontend']); console.log('Open http://127.0.0.1:5173 in your browser.'); }
  else console.log('Running without Studio. Press Ctrl+C to stop.');
}

main().catch((error) => { console.error(`\n${error.message}`); void shutdown(1); });
