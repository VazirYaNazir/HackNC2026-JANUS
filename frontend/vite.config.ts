import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
import { defineConfig } from 'vitest/config'

const stateDirectory = process.env.JANUS_STATE_DIR && resolve(process.env.JANUS_STATE_DIR).replaceAll('\\', '/')
const privateState = stateDirectory ? [`${stateDirectory.replace(/[?*[\]{}()!+|]/g, '\\$&')}/**`] : []

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5173, strictPort: true, allowedHosts: ['.trycloudflare.com'],
    fs: { deny: [
      '.env', '.env.*', '*.{crt,pem,key,p12,pfx,cer,der}', '.npmrc', '.yarnrc.yml', '**/.git/**',
      '**/.janus/**', '**/.validation-policy*.json', '**/*.db', '**/*.sqlite', '**/*.sqlite3',
      '**/*.{db,sqlite,sqlite3}-{wal,shm,journal}', '**/.venv/**',
      ...privateState,
    ] },
  },
  // @janus/design (lucide-react) lives outside this package; keep a single React instance.
  resolve: { dedupe: ['react', 'react-dom'] },
  test: {
    include: ['tests/**/*.test.{ts,tsx}'],
    coverage: { provider: 'v8', allowExternal: true, reporter: ['text', 'html', 'json-summary'], include: ['src/**/*.{ts,tsx}', '../shared/**/*.{ts,tsx}', '../PWA/src/**/*.{ts,tsx}'], exclude: ['**/main.tsx', '**/vite-env.d.ts'] },
  },
})
