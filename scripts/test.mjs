import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const mode = process.argv[2] ?? 'all';
if (!['all', 'unit', 'coverage', 'browser'].includes(mode)) throw new Error(`Unknown test mode: ${mode}`);
const cwd = fileURLToPath(new URL('..', import.meta.url));
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
function run(command, args) {
  return new Promise((resolve, reject) => {
    console.log(`\nRunning: ${command} ${args.join(' ')}`);
    const child = spawn(command, args, { cwd, stdio: 'inherit', shell: process.platform === 'win32' });
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolve() : reject(new Error(`${command} ${args.join(' ')} failed (${code})`)));
  });
}
async function pythonTests(folder) {
  const args = ['run', '--locked', '--directory', folder];
  await run('uv', [...args, 'coverage', 'run', '-m', 'unittest', 'discover', '-s', 'tests']);
  await run('uv', [...args, 'coverage', 'report', '-m']);
  if (mode === 'coverage') await run('uv', [...args, 'coverage', 'html']);
}
if (mode !== 'browser') {
  const results = await Promise.allSettled([
    pythonTests('agent'), pythonTests('backend'),
    run(npm, ['exec', '--', 'vitest', 'run', '--config', 'vitest.config.ts', ...(mode === 'unit' ? [] : ['--coverage'])]),
  ]);
  const failures = results.filter(result => result.status === 'rejected');
  if (failures.length) { failures.forEach(result => console.error(result.reason.message)); process.exit(1); }
}
if (mode === 'all' || mode === 'browser') {
  await run(npm, ['run', 'lint', '--workspace', 'frontend']);
  await run(npm, ['run', 'lint', '--workspace', '@janus/pwa']);
  await run(npm, ['exec', '--', 'eslint', '--config', 'PWA/eslint.config.js', 'shared']);
  await run(npm, ['run', 'build', '--workspace', 'frontend']);
  await run(npm, ['run', 'build', '--workspace', '@janus/pwa']);
  await run(npm, ['run', 'test:e2e', '--workspace', 'frontend']);
}
console.log(`\nJANUS ${mode} checks passed.`);
