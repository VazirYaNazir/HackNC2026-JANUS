import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

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
    ] },
  },
  test: {
    include: ['tests/**/*.test.{ts,tsx}'],
    coverage: { provider: 'v8', allowExternal: true, reporter: ['text', 'html', 'json-summary'], include: ['src/**/*.{ts,tsx}', '../shared/**/*.{ts,tsx}', '../PWA/src/**/*.{ts,tsx}'], exclude: ['**/main.tsx', '**/vite-env.d.ts'] },
  },
})
