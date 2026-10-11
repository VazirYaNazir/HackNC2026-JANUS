import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { describe, expect, it, vi } from 'vitest';

const root = fileURLToPath(new URL('../..', import.meta.url));
const privateFiles = ['.janus/controller.json', '.janus/devices.json', 'agent/.validation-policy.json',
  'agent/.validation-policy-full.json', 'backend/local.db', 'backend/local.db-wal', 'backend/local.sqlite',
  'backend/local.sqlite-shm', '.venv/local-worker.py', '.env', '.env.local', 'private.pem', 'private.key', '.git/config'];

describe('both development servers protect local launcher state', () => {
  it.each(['frontend', 'PWA'])('%s rejects private files and still serves the app', async project => {
    const fixture = await mkdtemp(join(root, '.vite-private-test-'));
    let server: Awaited<ReturnType<typeof createServer>> | undefined;
    vi.stubEnv('JANUS_PWA_HTTP', '1');
    vi.stubEnv('JANUS_STATE_DIR', join(fixture, 'private-state[1]'));
    const files = [...privateFiles, 'private-state[1]/controller.json'];
    try {
      for (const file of files) {
        const path = join(fixture, file);
        await mkdir(join(path, '..'), { recursive: true });
        await writeFile(path, 'private-fixture-do-not-serve');
      }
      server = await createServer({ root: join(root, project), logLevel: 'silent', clearScreen: false,
        server: { host: '127.0.0.1', port: 0, strictPort: false } });
      await server.listen();
      const address = server.httpServer!.address();
      if (!address || typeof address === 'string') throw new Error('Missing test HTTP address');
      const host = `http://127.0.0.1:${address.port}`;
      expect((await fetch(`${host}/src/main.tsx`)).status).toBe(200);
      for (const file of files) {
        for (const query of ['', '?raw', '?url']) {
          const response = await fetch(`${host}/@fs${join(fixture, file)}${query}`);
          expect(response.status, `${file}${query}`).toBe(403);
          expect(await response.text(), `${file}${query}`).not.toContain('private-fixture-do-not-serve');
        }
      }
    } finally {
      await server?.close(); await rm(fixture, { recursive: true, force: true }); vi.unstubAllEnvs();
    }
  }, 20000);
});
