
const { app, BrowserWindow, ipcMain, shell } = require('electron');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..');
const DEV_ORIGIN = 'http://127.0.0.1:5173';
// Studio links to setup docs; only those open (in the system browser). Everything else is denied.
const DOCS_PREFIX = 'https://github.com/VazirYaNazir/HackNC2026-Janus/';
const BACKEND_ORIGIN = 'http://127.0.0.1:8000';
let agentProcess = null;

// Only Studio's own window may use the desktop bridge, and only from the dev server's origin.
// The local credential never leaves the main process except over this IPC channel.
function trusted(event) {
  try { return !app.isPackaged && new URL(event.senderFrame.url).origin === DEV_ORIGIN; } catch { return false; }
}

function localConnection() {
  const text = fs.readFileSync(path.join(REPO_ROOT, 'backend', '.env'), 'utf8');
  const line = text.split(/\r?\n/).find((entry) => entry.startsWith('AGENT_DEVICES_JSON='));
  const device = JSON.parse(line.slice('AGENT_DEVICES_JSON='.length))[0];
  if (!device?.controller_token) throw new Error('No laptop is registered in backend/.env.');
  return { host: BACKEND_ORIGIN, token: device.controller_token, deviceId: device.device_id };
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
  agentProcess = spawn(process.execPath, [path.join(REPO_ROOT, 'scripts', 'start.mjs'), '--only=agent'], {
    cwd: REPO_ROOT,
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
    stdio: 'ignore',
    windowsHide: true,
  });
  agentProcess.on('exit', () => { agentProcess = null; });
  agentProcess.on('error', () => { agentProcess = null; });
  return { running: true };
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

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(DOCS_PREFIX)) void shell.openExternal(url);
    return { action: 'deny' };
  });

  win.once('ready-to-show', () => {
    win.show();
  });

  if (app.isPackaged) {
    win.loadFile(path.join(__dirname, '../dist/index.html'));
  } else {
    win.loadURL('http://127.0.0.1:5173');
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
