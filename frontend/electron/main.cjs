
const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..');
// Open only repository documentation and the exact official worker prerequisite link.
const DOCS_PATH = '/VazirYaNazir/HackNC2026-Janus/';
const DOCKER_DESKTOP_URL = 'https://www.docker.com/products/docker-desktop/';
const BACKEND_ORIGIN = 'http://127.0.0.1:8000';
let agentProcess = null;
let startingAgent = null;
let studioWindow = null;
let studioOrigin = null;

function externalSetupUrl(configured) {
  if (configured === DOCKER_DESKTOP_URL) return configured;
  if (typeof configured !== 'string' || /\s|\\/.test(configured)) return null;
  try {
    const url = new URL(configured);
    if (url.origin === 'https://github.com' && !url.username && !url.password
        && !url.search && url.pathname.startsWith(DOCS_PATH)) return url.href;
  } catch { /* Invalid links never leave the application. */ }
  return null;
}

function localUrl(configured, allowFragment = false) {
  if (typeof configured !== 'string' || /\s|\\/.test(configured)) throw new Error('Invalid local URL.');
  const url = new URL(configured);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)
      || url.username || url.password || url.pathname !== '/' || url.search || (!allowFragment && url.hash)) {
    throw new Error('JANUS must address a local HTTP server without credentials or a path.');
  }
  return url;
}

// Only Studio's own window may use the desktop bridge, and only from the dev server's origin.
// The local credential never leaves the main process except over this IPC channel.
function trusted(event) {
  try {
    return !app.isPackaged && studioWindow && !studioWindow.isDestroyed()
      && event.sender === studioWindow.webContents && event.senderFrame === studioWindow.webContents.mainFrame
      && new URL(event.senderFrame.url).origin === studioOrigin;
  } catch { return false; }
}

function validConnection(value) {
  if (!value || typeof value.token !== 'string' || value.token.length < 32 || value.token.length > 256
      || typeof value.deviceId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.deviceId)) {
    throw new Error('The local laptop registration is invalid.');
  }
  return { host: localUrl(value.host).origin, deviceId: value.deviceId, token: value.token };
}

function localConnection() {
  const selected = path.join(process.env.JANUS_STATE_DIR || path.join(REPO_ROOT, '.janus'), 'controller.json');
  try {
    const value = JSON.parse(fs.readFileSync(selected, 'utf8'));
    if (process.env.AGENT_DEVICE_ID && value.device_id !== process.env.AGENT_DEVICE_ID) {
      throw new Error('The selected laptop registration does not match this session.');
    }
    return validConnection({ host: value.host, deviceId: value.device_id, token: value.token });
  } catch (error) {
    // An invalid selected registration must never silently fall back to another laptop.
    if (error.code !== 'ENOENT') throw error;
  }
  let text = '';
  try { text = fs.readFileSync(path.join(REPO_ROOT, 'backend', '.env'), 'utf8'); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const line = text.split(/\r?\n/).find((entry) => entry.startsWith('AGENT_DEVICES_JSON='));
  const devices = JSON.parse(process.env.AGENT_DEVICES_JSON || line?.slice('AGENT_DEVICES_JSON='.length) || '[]');
  if (!Array.isArray(devices)) throw new Error('The local laptop registration is invalid.');
  const device = process.env.AGENT_DEVICE_ID
    ? devices.find((entry) => entry?.device_id === process.env.AGENT_DEVICE_ID)
    : devices.length === 1 ? devices[0] : null;
  if (!device) throw new Error('Select a registered laptop before starting JANUS.');
  return validConnection({ host: process.env.JANUS_BACKEND_URL || process.env.VITE_BACKEND_URL || BACKEND_ORIGIN,
    token: device.controller_token, deviceId: device.device_id });
}

ipcMain.handle('janus:local-connection', (event) => {
  if (!trusted(event)) throw new Error('Not available from this page.');
  try { return localConnection(); } catch { return null; }
});

// An agent already started by `npm start` (or an earlier click) is recorded in .janus/pids.json.
async function runningAgent() {
  try {
    const { liveAgent } = await import(pathToFileURL(path.join(REPO_ROOT, 'scripts', 'lib', 'common.mjs')).href);
    return liveAgent();
  } catch {
    return null;
  }
}

ipcMain.handle('janus:agent-status', async (event) => {
  if (!trusted(event)) throw new Error('Not available from this page.');
  return { running: agentProcess !== null || (await runningAgent()) !== null };
});

// Same orchestration script as `npm start`, so there is one place that knows how to launch the agent.
ipcMain.handle('janus:agent-start', async (event) => {
  if (!trusted(event)) throw new Error('Not available from this page.');
  // Never start a second agent for the same laptop: the backend would refuse it, and it would only add a process.
  if (agentProcess || (await runningAgent())) return { running: true, alreadyRunning: true };
  if (startingAgent) return startingAgent;
  startingAgent = (async () => {
    const connection = localConnection();
    const { response } = await dialog.showMessageBox(studioWindow, {
      type: 'warning', title: 'Allow JANUS laptop controls?',
      message: 'Allow JANUS controls and paired phones to use this laptop for this session?',
      detail: 'This enables mouse and volume control, screen sharing, locking the desktop, and process inspection. '
        + 'The included portable C worker uses Docker Desktop; JANUS may start an existing installation and prepare its image. '
        + 'Generated jobs can read and write only temporary worker files, spawn bounded worker processes, '
        + 'and request locally granted native controls through checked commands. The included worker cannot access laptop files, '
        + 'use the network, send data externally, request administrator access, or make persistent changes. '
        + 'A custom adapter must independently enforce any additional locally granted permissions. '
        + 'Job outputs may be shared with the backend and paired phones. '
        + 'Approval ends when the agent stops or JANUS exits.',
      buttons: ['Allow this session', 'Cancel'], defaultId: 1, cancelId: 1, noLink: true,
    });
    if (response !== 0) throw new Error('Laptop permissions declined. The agent was not started.');
    // Re-check ownership after the local dialog: navigation cannot inherit approval.
    if (!trusted(event)) throw new Error('The Studio page changed before laptop permissions were approved.');
    const backend = localUrl(connection.host);
    const child = spawn(process.execPath, [path.join(REPO_ROOT, 'scripts', 'start.mjs'), '--only=agent',
      '--startup-approval-stdin', '--device-id', connection.deviceId, '--backend-port', backend.port || '80'], {
      cwd: REPO_ROOT,
      env: { ...process.env, ELECTRON_RUN_AS_NODE: '1', AGENT_DEVICE_ID: connection.deviceId, JANUS_BACKEND_URL: connection.host },
      stdio: ['pipe', 'ignore', 'ignore'], windowsHide: true,
    });
    agentProcess = child;
    const clear = () => { if (agentProcess === child) agentProcess = null; };
    child.on('exit', clear);
    child.on('error', clear);
    child.stdin.on('error', () => { child.kill(); clear(); });
    await new Promise((resolve, reject) => {
      child.once('error', () => reject(new Error('Could not start the local JANUS agent.')));
      child.once('spawn', () => { child.stdin.end('JANUS_STARTUP_APPROVED\n'); resolve(); });
    });
    return { running: true };
  })();
  try { return await startingAgent; } finally { startingAgent = null; }
});

app.on('before-quit', () => { if (agentProcess) agentProcess.kill(); });

function supportsWindowsBackdrop() {
  if (process.platform !== 'win32') return false;

  const build = Number.parseInt(os.release().split('.')[2] ?? '0', 10);
  return Number.isFinite(build) && build >= 22621;
}

function createWindow() {
  const useNativeBackdrop = supportsWindowsBackdrop();
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    show: false,
    autoHideMenuBar: true,
    icon: path.join(__dirname, '../public', process.platform === 'win32' ? 'favicon.ico' : 'icon-512.png'),
    backgroundColor: useNativeBackdrop ? '#00000000' : '#11151c',
    ...(useNativeBackdrop ? { backgroundMaterial: 'acrylic' } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  studioWindow = win;

  win.webContents.setWindowOpenHandler(({ url }) => {
    const setupUrl = externalSetupUrl(url);
    if (setupUrl) void shell.openExternal(setupUrl);
    return { action: 'deny' };
  });

  win.once('ready-to-show', () => {
    win.show();
  });

  if (app.isPackaged) {
    win.loadFile(path.join(__dirname, '../dist/index.html'));
  } else {
    const url = localUrl(process.env.JANUS_STUDIO_URL || 'http://127.0.0.1:5173', true);
    studioOrigin = url.origin;
    win.loadURL(url.href);
  }
}

app.whenReady().then(() => {
  if (process.platform === 'darwin' && app.dock) app.dock.setIcon(path.join(__dirname, '../public/icon-512.png'));
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
