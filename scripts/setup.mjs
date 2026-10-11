// First-time (and re-runnable) setup: preflight, dependencies, backend/.env, native tools.
//   npm run setup [-- --skip-native] [-- --pwa-url=https://your-pwa.vercel.app]
import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { doctor, report } from './doctor.mjs';
import { ENV_EXAMPLE, ENV_FILE, ROOT, parseEnv, restrictToOwner, run } from './lib/common.mjs';

const args = process.argv.slice(2);
const flag = (name) => args.find((arg) => arg.startsWith(`--${name}=`))?.split('=').slice(1).join('=');
const defaults = JSON.parse(readFileSync(new URL('./defaults.json', import.meta.url), 'utf8'));
const pwaUrl = flag('pwa-url') || process.env.JANUS_PWA_URL || defaults.pwaPublicUrl;
const token = () => randomBytes(32).toString('base64url');

/** Adds keys from .env.example that the existing file lacks; never changes a value that is already set. */
export function mergeEnv(existing, example) {
  const have = parseEnv(existing);
  const missing = example.split(/\r?\n/).filter((line) => {
    const match = /^([A-Za-z_][A-Za-z0-9_]*)=/.exec(line);
    return match && !have.has(match[1]);
  });
  if (!missing.length) return { text: existing, added: [] };
  const separator = existing.length && !existing.endsWith('\n') ? '\n' : '';
  return { text: `${existing}${separator}\n# Added by npm run setup\n${missing.join('\n')}\n`, added: missing.map((line) => line.split('=')[0]) };
}

/** Fills generated values only where the file has none. */
export function fillGenerated(text, { pwaUrl: pwa, deviceId = 'my-laptop' } = {}) {
  const env = parseEnv(text);
  const set = (current, key, value) => current.replace(new RegExp(`^${key}=.*$`, 'm'), () => `${key}=${value}`);
  const filled = [];
  let out = text;
  if (!env.get('ADMIN_SECRET')) { out = set(out, 'ADMIN_SECRET', token()); filled.push('ADMIN_SECRET'); }
  const devices = env.get('AGENT_DEVICES_JSON');
  if (!env.get('AGENT_DEVICES_FILE') && (!devices || devices.trim() === '[]')) {
    const device = [{ device_id: deviceId, owner_id: 'me', agent_token: token(), controller_token: token() }];
    out = set(out, 'AGENT_DEVICES_JSON', JSON.stringify(device));
    filled.push('AGENT_DEVICES_JSON');
  }
  if (pwa && !env.get('PWA_PUBLIC_URL')) { out = set(out, 'PWA_PUBLIC_URL', pwa.replace(/\/+$/, '')); filled.push('PWA_PUBLIC_URL'); }
  return { text: out, filled };
}

async function main() {
  const preflight = await doctor({ beforeSetup: true });
  if (!report(preflight)) { console.error('\nFix the problems above and run `npm run setup` again.'); process.exit(1); }

  if (!existsSync(path.join(ROOT, 'node_modules', '.package-lock.json')) || args.includes('--reinstall')) {
    console.log('Installing JavaScript dependencies…');
    await run('npm', ['ci']);
  } else console.log('JavaScript dependencies already installed (use --reinstall to redo).');
  for (const folder of ['backend', 'agent']) {
    console.log(`Installing ${folder} dependencies…`);
    await run('uv', ['sync', '--locked'], { cwd: path.join(ROOT, folder) });
  }

  const example = readFileSync(ENV_EXAMPLE, 'utf8');
  const created = !existsSync(ENV_FILE);
  const merged = mergeEnv(created ? '' : readFileSync(ENV_FILE, 'utf8'), example);
  const filled = fillGenerated(created ? example : merged.text, { pwaUrl });
  writeFileSync(ENV_FILE, filled.text, { mode: 0o600 });
  if (!restrictToOwner(ENV_FILE)) console.warn('! Could not restrict backend/.env to your user; check its permissions — it holds credentials.');
  console.log(created ? 'Created backend/.env.' : `Updated backend/.env (added: ${merged.added.join(', ') || 'nothing'}).`);
  if (filled.filled.length) console.log(`Generated values for: ${filled.filled.join(', ')}.`);
  if (!pwaUrl) console.log('! PWA_PUBLIC_URL is empty, so pairing QR codes will not open the hosted PWA directly. Re-run with --pwa-url=https://… or set it in scripts/defaults.json.');

  if (!args.includes('--skip-native')) {
    console.log('Building native tools (the first run may download a compiler)…');
    await run('uv', ['run', '--no-sync', 'python', '-m', 'agent.main', 'build'], { cwd: path.join(ROOT, 'agent') }).catch((error) => {
      throw new Error(`${error.message}
If Janus or its agent is running, stop it first (Windows locks the running native tools), then re-run \`npm run setup\`.`);
    });
  }
  console.log('\nSetup complete. Start everything with:  npm start');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(`\nSetup failed: ${error.message}`); process.exit(1); });
}
