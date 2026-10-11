
const { app, BrowserWindow } = require('electron');
const os = require('node:os');
const path = require('node:path');

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
