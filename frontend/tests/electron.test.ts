// Execute the real main process with a mocked Electron boundary; no desktop or credentials are needed.
import { EventEmitter } from 'node:events';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const root = fileURLToPath(new URL('../..', import.meta.url));
const electronDirectory = path.join(root, 'frontend', 'electron');
const source = readFileSync(path.join(electronDirectory, 'main.cjs'), 'utf8');
const token = 'controller-credential-'.padEnd(48, 'x');
const selected = { host: 'http://127.0.0.1:8123', device_id: 'my-laptop', token };

function boot(options: {
  env?: Record<string, string>, files?: Record<string, string>, response?: number,
  packaged?: boolean, spawnError?: boolean, platform?: string,
} = {}) {
  const env = options.env ?? {};
  const files = options.files ?? { [path.join(root, '.janus', 'controller.json')]: JSON.stringify(selected) };
  const handlers = new Map<string, (event: unknown) => Promise<unknown>>();
  const appHandlers = new Map<string, () => void>();
  const child = Object.assign(new EventEmitter(), {
    stdin: Object.assign(new EventEmitter(), { end: vi.fn() }), kill: vi.fn(),
  });
  const spawn = vi.fn(() => {
    queueMicrotask(() => child.emit(options.spawnError ? 'error' : 'spawn', new Error('secret spawn diagnostic')));
    return child;
  });
  const dialog = { showMessageBox: vi.fn<(window: unknown, settings: Record<string, unknown>) => Promise<{ response: number }>>()
    .mockResolvedValue({ response: options.response ?? 1 }) };
  const shell = { openExternal: vi.fn() };
  class Window {
    static windows: Window[] = [];
    webContents = { mainFrame: { url: '', parent: null }, setWindowOpenHandler: vi.fn() };
    destroyed = false;
    loadURL = vi.fn((url: string) => { this.webContents.mainFrame.url = url; });
    loadFile = vi.fn();
    once = vi.fn();
    show = vi.fn();
    constructor() { Window.windows.push(this); }
    isDestroyed() { return this.destroyed; }
    static getAllWindows() { return Window.windows; }
  }
  const app = { isPackaged: options.packaged ?? false, dock: { setIcon: vi.fn() }, quit: vi.fn(),
    on: vi.fn((event: string, callback: () => void) => appHandlers.set(event, callback)),
    whenReady: () => ({ then: (callback: () => void) => callback() }),
  };
  const read = vi.fn((file: string) => {
    if (!(file in files)) throw Object.assign(new Error('missing file'), { code: 'ENOENT' });
    return files[file];
  });
  vm.runInNewContext(source, {
    __dirname: electronDirectory, URL,
    process: { env, execPath: '/usr/bin/electron', platform: options.platform ?? 'darwin' },
    require: (name: string) => {
      if (name === 'electron') return { app, BrowserWindow: Window, dialog, ipcMain: {
        handle: (channel: string, handler: (event: unknown) => Promise<unknown>) => handlers.set(channel, handler),
      }, shell };
      if (name === 'node:child_process') return { spawn };
      if (name === 'node:fs') return { readFileSync: read };
      if (name === 'node:os') return { release: () => '10.0.22621' };
      if (name === 'node:path') return path;
      if (name === 'node:url') return { pathToFileURL: (value: string) => new URL(`file://${value}`) };
      throw new Error(`Unexpected main-process import: ${name}`);
    },
  });
  const win = Window.windows[0];
  const event = () => ({ sender: win.webContents, senderFrame: win.webContents.mainFrame });
  const invoke = (channel: string, sender = event()) => Promise.resolve().then(() => handlers.get(channel)?.(sender));
  return { app, appHandlers, child, dialog, event, invoke, read, shell, spawn, win };
}

describe('Electron local Studio bridge', () => {
  it('trusts the selected configurable loopback Studio origin and keeps bootstrap fragments', async () => {
    const url = 'http://localhost:5317/#janus-start=http%3A%2F%2F127.0.0.1%3A4123&nonce=one-use-bootstrap';
    const desktop = boot({ env: { JANUS_STUDIO_URL: url } });
    expect(desktop.win.loadURL).toHaveBeenCalledWith(url);
    await expect(desktop.invoke('janus:local-connection')).resolves.toEqual({ host: selected.host, deviceId: selected.device_id, token });
    expect(url).not.toContain(token);
  });

  it.each(['https://localhost:5173', 'http://evil.example', 'http://user:secret@localhost:5173',
    'http://127.0.0.1:5173/path', 'http://127.0.0.1:5173?token=secret',
    'http://127.0.0.1:5173\\evil', ' http://127.0.0.1:5173'])('rejects malformed Studio configuration %s', url => {
    expect(() => boot({ env: { JANUS_STUDIO_URL: url } })).toThrow();
  });

  it('denies packaged pages, same-origin subframes, other windows, remote navigation, and destroyed windows', async () => {
    const packaged = boot({ packaged: true });
    await expect(packaged.invoke('janus:local-connection')).rejects.toThrow('Not available');
    const desktop = boot();
    const event = desktop.event();
    await expect(desktop.invoke('janus:local-connection', { ...event, senderFrame: { ...event.senderFrame } })).rejects.toThrow('Not available');
    await expect(desktop.invoke('janus:agent-start', { ...event, sender: { ...event.sender } })).rejects.toThrow('Not available');
    desktop.win.webContents.mainFrame.url = 'https://evil.example/';
    await expect(desktop.invoke('janus:agent-status')).rejects.toThrow('Not available');
    desktop.win.webContents.mainFrame.url = 'http://127.0.0.1:5173';
    desktop.win.destroyed = true;
    await expect(desktop.invoke('janus:agent-start')).rejects.toThrow('Not available');
    expect(desktop.dialog.showMessageBox).not.toHaveBeenCalled();
    expect(desktop.spawn).not.toHaveBeenCalled();
  });

  it('uses the selected private controller file before legacy registrations', async () => {
    const desktop = boot({ files: {
      [path.join(root, '.janus', 'controller.json')]: JSON.stringify(selected),
      [path.join(root, 'backend', '.env')]: 'AGENT_DEVICES_JSON=not valid JSON',
    } });
    await expect(desktop.invoke('janus:local-connection')).resolves.toMatchObject({ deviceId: 'my-laptop', token });
    expect(desktop.read).toHaveBeenCalledTimes(1);
  });

  it('respects the selected state directory and device', async () => {
    const directory = path.join(root, '.janus-test-state');
    const desktop = boot({ env: { JANUS_STATE_DIR: directory, AGENT_DEVICE_ID: 'my-laptop' }, files: {
      [path.join(directory, 'controller.json')]: JSON.stringify(selected),
    } });
    await expect(desktop.invoke('janus:local-connection')).resolves.toMatchObject({ host: selected.host });
    const mismatch = boot({ env: { AGENT_DEVICE_ID: 'another-laptop' } });
    await expect(mismatch.invoke('janus:local-connection')).resolves.toBeNull();
  });

  it.each([{ ...selected, token: 'short' }, { ...selected, token: 123 }, { ...selected, token: 'x'.repeat(257) },
    { ...selected, device_id: '../laptop' }, { ...selected, device_id: null },
    { ...selected, host: 'https://remote.example' }, { ...selected, host: 'http://secret@localhost:8000' },
    { ...selected, host: 'http://localhost:8000?token=secret' }])('refuses invalid selected credentials without legacy fallback %#', async connection => {
    const desktop = boot({ response: 0, files: {
      [path.join(root, '.janus', 'controller.json')]: JSON.stringify(connection),
      [path.join(root, 'backend', '.env')]: `AGENT_DEVICES_JSON=${JSON.stringify([{ device_id: 'legacy', controller_token: token }])}`,
    } });
    await expect(desktop.invoke('janus:local-connection')).resolves.toBeNull();
    await expect(desktop.invoke('janus:agent-start')).rejects.toThrow();
    expect(desktop.spawn).not.toHaveBeenCalled();
    expect(desktop.dialog.showMessageBox).not.toHaveBeenCalled();
  });

  it('selects the configured legacy laptop and backend instead of the first registration', async () => {
    const devices = [{ device_id: 'first', controller_token: 'f'.repeat(48) }, { device_id: 'second', controller_token: token }];
    const files = { [path.join(root, 'backend', '.env')]: `AGENT_DEVICES_JSON=${JSON.stringify(devices)}` };
    const desktop = boot({ env: { AGENT_DEVICE_ID: 'second', JANUS_BACKEND_URL: 'http://localhost:8181' }, files });
    await expect(desktop.invoke('janus:local-connection')).resolves.toEqual({ host: 'http://localhost:8181', deviceId: 'second', token });
    const ambiguous = boot({ files });
    await expect(ambiguous.invoke('janus:local-connection')).resolves.toBeNull();
  });

  it('accepts inherited selected registrations without requiring a legacy env file', async () => {
    const desktop = boot({ files: {}, env: {
      AGENT_DEVICES_JSON: JSON.stringify([{ device_id: 'selected', controller_token: token }]),
      AGENT_DEVICE_ID: 'selected', VITE_BACKEND_URL: 'http://localhost:8282',
    } });
    await expect(desktop.invoke('janus:local-connection')).resolves.toEqual({ host: 'http://localhost:8282', deviceId: 'selected', token });
  });

  it('uses the sole legacy laptop with the default local backend', async () => {
    const desktop = boot({ files: {
      [path.join(root, 'backend', '.env')]: `AGENT_DEVICES_JSON=${JSON.stringify([{ device_id: 'legacy', controller_token: token }])}`,
    } });
    await expect(desktop.invoke('janus:local-connection')).resolves.toEqual({ host: 'http://127.0.0.1:8000', deviceId: 'legacy', token });
  });

  it('fails closed on corrupted selected JSON', async () => {
    const desktop = boot({ files: {
      [path.join(root, '.janus', 'controller.json')]: '{bad JSON',
      [path.join(root, 'backend', '.env')]: `AGENT_DEVICES_JSON=${JSON.stringify([{ device_id: 'legacy', controller_token: token }])}`,
    } });
    await expect(desktop.invoke('janus:local-connection')).resolves.toBeNull();
    expect(desktop.read).toHaveBeenCalledTimes(1);
  });
});

describe('Electron session permission approval', () => {
  it.each([-1, 1, 2])('treats every dialog response except explicit approval as cancellation: %s', async response => {
    const desktop = boot({ response });
    await expect(desktop.invoke('janus:agent-start')).rejects.toThrow('Laptop permissions declined');
    expect(desktop.spawn).not.toHaveBeenCalled();
  });
  it('requires a local dialog with cancellation selected by default', async () => {
    const desktop = boot();
    await expect(desktop.invoke('janus:agent-start')).rejects.toThrow('Laptop permissions declined');
    expect(desktop.spawn).not.toHaveBeenCalled();
    expect(desktop.dialog.showMessageBox).toHaveBeenCalledWith(desktop.win, expect.objectContaining({ defaultId: 1, cancelId: 1 }));
    const disclosure = String(desktop.dialog.showMessageBox.mock.calls[0][1].detail);
    for (const permission of ['mouse', 'volume', 'screen', 'locking the desktop', 'process inspection', 'Docker Desktop',
      'start an existing installation', 'prepare its image', 'read and write only temporary worker files', 'spawn bounded worker processes',
      'locally granted native controls through checked commands', 'Job outputs', 'paired phones', 'Approval ends']) {
      expect(disclosure).toContain(permission);
    }
    expect(disclosure).toContain('The included worker cannot access laptop files, use the network, send data externally, request administrator access, or make persistent changes.');
    expect(disclosure).toContain('A custom adapter must independently enforce any additional locally granted permissions.');
    expect(disclosure).not.toContain('require an installed isolation adapter');
  });

  it('starts the wrapper only after approval and sends one private stdin marker', async () => {
    const desktop = boot({ response: 0 });
    await expect(desktop.invoke('janus:agent-start')).resolves.toEqual({ running: true });
    expect(desktop.spawn).toHaveBeenCalledWith('/usr/bin/electron', [path.join(root, 'scripts', 'start.mjs'),
      '--only=agent', '--startup-approval-stdin', '--device-id', 'my-laptop', '--backend-port', '8123'], expect.objectContaining({
      stdio: ['pipe', 'ignore', 'ignore'], windowsHide: true,
      env: expect.objectContaining({ ELECTRON_RUN_AS_NODE: '1', AGENT_DEVICE_ID: 'my-laptop', JANUS_BACKEND_URL: selected.host }),
    }));
    expect(desktop.child.stdin.end).toHaveBeenCalledExactlyOnceWith('JANUS_STARTUP_APPROVED\n');
    expect(JSON.stringify(desktop.spawn.mock.calls)).not.toContain(token);
    await expect(desktop.invoke('janus:agent-status')).resolves.toEqual({ running: true });
    desktop.appHandlers.get('before-quit')?.();
    expect(desktop.child.kill).toHaveBeenCalledTimes(1);
  });

  it('shares concurrent approvals and does not launch duplicate agents', async () => {
    const desktop = boot({ response: 0 });
    await Promise.all([desktop.invoke('janus:agent-start'), desktop.invoke('janus:agent-start')]);
    expect(desktop.dialog.showMessageBox).toHaveBeenCalledTimes(1);
    expect(desktop.spawn).toHaveBeenCalledTimes(1);
    await expect(desktop.invoke('janus:agent-start')).resolves.toEqual({ running: true, alreadyRunning: true });
    expect(desktop.dialog.showMessageBox).toHaveBeenCalledTimes(1);
  });

  it('rechecks the Studio frame after approval and refuses navigation during the dialog', async () => {
    const desktop = boot();
    desktop.dialog.showMessageBox.mockImplementation(async () => {
      desktop.win.webContents.mainFrame.url = 'https://evil.example/';
      return { response: 0 };
    });
    await expect(desktop.invoke('janus:agent-start')).rejects.toThrow('Studio page changed');
    expect(desktop.spawn).not.toHaveBeenCalled();
  });

  it('reports spawn failure without leaking diagnostics and clears running state', async () => {
    const desktop = boot({ response: 0, spawnError: true });
    await expect(desktop.invoke('janus:agent-start')).rejects.toThrow('Could not start the local JANUS agent.');
    expect(desktop.child.stdin.end).not.toHaveBeenCalled();
    await expect(desktop.invoke('janus:agent-status')).resolves.toEqual({ running: false });
  });

  it('revokes process state when the child exits', async () => {
    const desktop = boot({ response: 0 });
    await desktop.invoke('janus:agent-start');
    desktop.child.emit('exit', 1);
    await expect(desktop.invoke('janus:agent-status')).resolves.toEqual({ running: false });
  });

  it('kills a child if its approval pipe fails', async () => {
    const desktop = boot({ response: 0 });
    await desktop.invoke('janus:agent-start');
    desktop.child.stdin.emit('error', new Error('approval pipe closed'));
    expect(desktop.child.kill).toHaveBeenCalledTimes(1);
    await expect(desktop.invoke('janus:agent-status')).resolves.toEqual({ running: false });
  });
});

describe('Electron external setup links', () => {
  const docker = 'https://www.docker.com/products/docker-desktop/';
  const docs = 'https://github.com/VazirYaNazir/HackNC2026-Janus/blob/main/README.md#isolation-adapter';

  it.each([docker, docs])('opens the approved setup link in the system browser: %s', url => {
    const desktop = boot();
    const open = desktop.win.webContents.setWindowOpenHandler.mock.calls[0][0] as (input: { url: string }) => unknown;
    expect(open({ url })).toEqual({ action: 'deny' });
    expect(desktop.shell.openExternal).toHaveBeenCalledExactlyOnceWith(url);
  });

  it.each([
    'http://www.docker.com/products/docker-desktop/',
    'https://docker.com/products/docker-desktop/',
    'https://www.docker.com.evil.test/products/docker-desktop/',
    'https://evil-www.docker.com/products/docker-desktop/',
    'https://user:secret@www.docker.com/products/docker-desktop/',
    'https://www.docker.com@evil.test/products/docker-desktop/',
    'https://www.docker.com:8443/products/docker-desktop/',
    'https://www.docker.com:443/products/docker-desktop/',
    'https://www.docker.com/products/docker-desktop',
    'https://www.docker.com/products/docker-desktop/evil',
    'https://www.docker.com/products/docker-desktop-evil/',
    `${docker}?redirect=https://evil.test`, `${docker}#token=secret`,
    'https://www.docker.com/other/../products/docker-desktop/',
    'https://www.docker.com/products/docker-desktop/%2f..%2f..%2fevil',
    ` ${docker}`, `${docker}\n`, 'https://www.docker.com\\products\\docker-desktop\\',
    'file:///tmp/install-docker', 'javascript:alert(1)', '//evil.test', 'not a URL',
    'https://github.com.evil.test/VazirYaNazir/HackNC2026-Janus/README.md',
    'https://user:secret@github.com/VazirYaNazir/HackNC2026-Janus/README.md',
    'https://github.com/VazirYaNazir/HackNC2026-Janus/../../attacker/project',
    'https://github.com/VazirYaNazir/HackNC2026-Janus/%2e%2e/%2e%2e/attacker/project',
    'https://github.com/VazirYaNazir/HackNC2026-Janus/README.md?redirect=https://evil.test',
    'https://github.com/VazirYaNazir/HackNC2026-Janus-evil/README.md',
  ])('denies unapproved or malformed external links: %s', url => {
    const desktop = boot();
    const open = desktop.win.webContents.setWindowOpenHandler.mock.calls[0][0] as (input: { url: string }) => unknown;
    expect(open({ url })).toEqual({ action: 'deny' });
    expect(desktop.shell.openExternal).not.toHaveBeenCalled();
  });
});
