const { spawn } = require('node:child_process');
const path = require('node:path');
const electronPath = require('electron');

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const electron = spawn(electronPath, ['.'], {
  cwd: path.join(__dirname, '..'),
  env,
  stdio: 'inherit',
});

electron.on('error', (error) => {
  console.error('Unable to start Electron:', error);
  process.exitCode = 1;
});

electron.on('exit', (code) => {
  process.exit(code ?? 1);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    if (!electron.killed) {
      electron.kill(signal);
    }
  });
}
