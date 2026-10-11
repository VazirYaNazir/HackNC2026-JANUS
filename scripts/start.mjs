// Starts Janus: backend, laptop agent and Studio (Electron). Studio's "Start agent" button runs this same
// script with --only=agent, so there is a single orchestration path.
//   npm start [-- --pwa] [-- --tunnel] [-- --studio=electron|browser|none] [-- --no-agent] [-- --only=agent]
//   --pwa     also run the PWA dev server on :5174 (off by default; the phone uses the hosted PWA)
//   --tunnel  expose the backend through a Cloudflare tunnel for phone pairing (off by default)
import { spawn } from 'node:child_process';
import path from 'node:path';
import { doctor, report } from './doctor.mjs';
import { ensureCloudflared, BIN_DIR } from './lib/cloudflared.mjs';
import { ROOT, isJanusProcess, isWindows, killTree, listenerPid, portInUse, processName, readDevices, readPids, writePids } from './lib/common.mjs';

const args = process.argv.slice(2);
const flag = (name) => args.find((arg) => arg.startsWith(`--${name}=`))?.split('=').slice(1).join('=');
const only = flag('only');
const wantPwa = args.includes('--pwa');
const wantTunnel = args.includes('--tunnel');
const wantAgent = !args.includes('--no-agent');
const studio = flag('studio') ?? 'electron';
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
  writePids({ ...readPids(), [key]: child.pid });
}

function launch(name, command, commandArgs, { cwd = ROOT, env = {} } = {}) {
  const child = spawn(command, commandArgs, { cwd, env: { ...process.env, ...env }, shell: isWindows, detached: !isWindows, stdio: ['ignore', 'pipe', 'pipe'] });
  const prefix = `[${name}] `;
  for (const stream of [child.stdout, child.stderr]) {
    let pending = '';
    stream.on('data', (chunk) => {
      const lines = (pending + chunk).split(/\r?\n/);
      pending = lines.pop() ?? '';
      for (const line of lines) console.log(prefix + line);
    });
  }
  child.on('error', (error) => { console.error(`${prefix}failed to start: ${error.message}`); void shutdown(1); });
  child.on('exit', (code) => {
    children.delete(name);
    if (!shuttingDown) {
      console.log(`${prefix}exited (${code})`);
      // Closing Studio, or losing the backend, ends the session; a crashed agent does not.
      if (name === 'studio' || name === 'backend' || only) void shutdown(code ?? 0);
    }
  });
  track(name, child);
  return child;
}

async function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children.values()) if (child.pid) killTree(child.pid);
  const pids = readPids();
  for (const key of pidKeys) delete pids[key];
  writePids(pids);
  process.exit(code);
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => void shutdown(0));

/** Stop leftovers from a previous `npm start`; anything else holding a port aborts with an explanation. */
async function claimPorts(ports) {
  for (const port of ports) {
    if (!(await portInUse(port))) continue;
    const pid = listenerPid(port);
    if (pid && isJanusProcess(pid)) {
      console.log(`Stopping leftover Janus process on port ${port} (PID ${pid})…`);
      killTree(pid);
      for (let i = 0; i < 20 && (await portInUse(port)); i += 1) await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (await portInUse(port)) {
      const holder = pid ? `${processName(pid)} (PID ${pid})` : 'an unknown process';
      throw new Error(`Port ${port} is in use by ${holder}, which Janus did not start. Close it and try again.`);
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

async function main() {
  const ports = only ? [] : [8000, ...(studio === 'none' ? [] : [5173]), ...(wantPwa ? [5174] : [])];
  const preflight = await doctor({ tunnel: wantTunnel, pwa: wantPwa, ports: false });
  if (!report(preflight)) process.exit(1);
  await claimPorts(ports);

  const registered = device();
  const agentEnv = { AGENT_TOKEN: registered.agent_token };
  const startAgent = () => launch('agent', 'uv', ['run', '--locked', 'python', '-m', 'agent.main', 'connect', '--url', `ws://127.0.0.1:8000/ws/agents/${registered.device_id}`, '--device-id', registered.device_id, '--native-tools'], { cwd: path.join(ROOT, 'agent'), env: agentEnv });

  if (only === 'agent') { await waitForBackend(); startAgent(); return; }

  const backendEnv = {};
  if (wantTunnel) {
    await ensureCloudflared();
    backendEnv.JANUS_AUTO_TUNNEL = '1';
    backendEnv.PATH = `${BIN_DIR}${path.delimiter}${process.env.PATH}`;
    console.log('Tunnel enabled: the backend will be reachable on the public internet (still behind its credentials) until you stop Janus.');
  }
  launch('backend', 'uv', ['run', '--locked', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--env-file', '.env'], { cwd: path.join(ROOT, 'backend'), env: backendEnv });
  await waitForBackend();
  console.log(`Backend ready at ${BACKEND}`);

  if (wantAgent) startAgent();
  if (wantPwa) launch('pwa', 'npm', ['run', 'dev', '--workspace', '@janus/pwa', '--', '--host', '0.0.0.0', '--port', '5174', '--strictPort']);
  if (studio === 'electron') launch('studio', 'npm', ['run', 'electron', '--workspace', 'frontend']);
  else if (studio === 'browser') { launch('studio', 'npm', ['run', 'dev', '--workspace', 'frontend']); console.log('Open http://127.0.0.1:5173 in your browser.'); }
  else console.log('Running without Studio. Press Ctrl+C to stop.');
}

main().catch((error) => { console.error(`\n${error.message}`); void shutdown(1); });
