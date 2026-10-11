// First-time (and re-runnable) setup: preflight, dependencies, backend/.env, native tools.
//   npm run setup [-- --skip-native] [-- --pwa-url=https://your-pwa.vercel.app]
import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { aiChecks, doctor, report } from './doctor.mjs';
import { ENV_EXAMPLE, ENV_FILE, ROOT, makeFlags, parseEnv, restrictToOwner, run } from './lib/common.mjs';

const flags = makeFlags();
const defaults = JSON.parse(readFileSync(new URL('./defaults.json', import.meta.url), 'utf8'));
const pwaUrl = flags.value('pwa-url') || process.env.JANUS_PWA_URL || defaults.pwaPublicUrl;
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

/** Sets KEY=value on its existing line (the key must already be present). */
export function setEnvValue(text, key, value) {
  return text.replace(new RegExp(`^${key}=.*$`, 'm'), () => `${key}=${value}`);
}

export const looksLikeApiKey = (value) => /^\S{16,200}$/.test(value);

/** Reads a line without echoing it (the key is a credential). Enter alone skips. */
function promptSecret(question) {
  return new Promise((resolve) => {
    process.stdout.write(question);
    const { stdin } = process;
    let typed = '';
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding('utf8');
    const onData = (chunk) => {
      for (const char of chunk) {
        if (char === '\r' || char === '\n') { stdin.setRawMode(false); stdin.pause(); stdin.off('data', onData); process.stdout.write('\n'); return resolve(typed.trim()); }
        if (char === '\u0003') { stdin.setRawMode(false); process.stdout.write('\n'); process.exit(130); }
        if (char === '\u007f' || char === '\b') typed = typed.slice(0, -1);
        else typed += char;
      }
    };
    stdin.on('data', onData);
  });
}

/** Fills generated values only where the file has none. */
export function fillGenerated(text, { pwaUrl: pwa, deviceId = 'my-laptop' } = {}) {
  const env = parseEnv(text);
  // Replaces the key's line, or appends one when the file has none (never silently drops a generated secret).
  const set = (current, key, value) => {
    const line = new RegExp(`^${key}=.*$`, 'm');
    if (line.test(current)) return current.replace(line, () => `${key}=${value}`);
    return `${current}${current.endsWith('\n') || !current ? '' : '\n'}${key}=${value}\n`;
  };
  const filled = [];
  let out = text;
  if (!env.get('ADMIN_SECRET')) { out = set(out, 'ADMIN_SECRET', token()); filled.push('ADMIN_SECRET'); }
  const devices = env.get('AGENT_DEVICES_JSON');
  if (!env.get('AGENT_DEVICES_FILE') && (!devices || devices.trim() === '[]')) {
    const device = [{ device_id: deviceId, owner_id: 'me', agent_token: token(), controller_token: token() }];
    out = set(out, 'AGENT_DEVICES_JSON', JSON.stringify(device));
    filled.push('AGENT_DEVICES_JSON');
  }
  // Authenticates the iMessage sidecar to the backend's loopback-only messaging endpoint.
  if (!env.get('MESSAGING_SERVICE_TOKEN')) { out = set(out, 'MESSAGING_SERVICE_TOKEN', token()); filled.push('MESSAGING_SERVICE_TOKEN'); }
  if (pwa && !env.get('PWA_PUBLIC_URL')) { out = set(out, 'PWA_PUBLIC_URL', pwa.replace(/\/+$/, '')); filled.push('PWA_PUBLIC_URL'); }
  return { text: out, filled };
}

async function main() {
  const preflight = await doctor({ beforeSetup: true });
  if (!report(preflight)) { console.error('\nFix the problems above and run `npm run setup` again.'); process.exit(1); }

  if (!existsSync(path.join(ROOT, 'node_modules', '.package-lock.json')) || flags.has('reinstall')) {
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
  // Optional: a Gemini key enables AI-generated widgets. Never taken from argv, so it stays out of shell history.
  const hasKey = ['GEMINI_API_KEY', 'GOOGLE_API_KEY'].some((key) => parseEnv(filled.text).get(key));
  if (!hasKey) {
    const interactive = process.stdin.isTTY && process.stdout.isTTY && !process.env.CI && !flags.has('no-prompt');
    const key = process.env.JANUS_GEMINI_KEY || (interactive ? await promptSecret('Gemini API key for AI-generated widgets (optional; press Enter to skip): ') : '');
    if (looksLikeApiKey(key)) { filled.text = setEnvValue(filled.text, 'GEMINI_API_KEY', key); filled.filled.push('GEMINI_API_KEY'); }
    else if (key) console.warn('! That does not look like an API key; skipped. Add GEMINI_API_KEY to backend/.env later.');
  }
  writeFileSync(ENV_FILE, filled.text, { mode: 0o600 });
  if (!restrictToOwner(ENV_FILE)) console.warn('! Could not restrict backend/.env to your user; check its permissions — it holds credentials.');
  console.log(created ? 'Created backend/.env.' : `Updated backend/.env (added: ${merged.added.join(', ') || 'nothing'}).`);
  if (filled.filled.length) console.log(`Generated values for: ${filled.filled.join(', ')}.`);
  if (!pwaUrl) console.log('! PWA_PUBLIC_URL is empty, so pairing QR codes will not open the hosted PWA directly. Re-run with --pwa-url=https://… or set it in scripts/defaults.json.');

  if (!flags.has('skip-native')) {
    console.log('Building native tools (the first run may download a compiler)…');
    await run('uv', ['run', '--no-sync', 'python', '-m', 'agent.main', 'build'], { cwd: path.join(ROOT, 'agent') }).catch((error) => {
      throw new Error(`${error.message}
If JANUS or its agent is running, stop it first (Windows locks the running native tools), then re-run \`npm run setup\`.`);
    });
  }
  report(aiChecks());
  console.log('\nSetup complete. Start everything with:  npm start');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(`\nSetup failed: ${error.message}`); process.exit(1); });
}
