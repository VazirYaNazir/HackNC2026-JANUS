import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  // @janus/design (lucide-react) lives outside this package; keep a single React instance.
  resolve: { dedupe: ['react', 'react-dom'] },
  test: {
    include: ['tests/**/*.test.{ts,tsx}'],
    coverage: { provider: 'v8', allowExternal: true, reporter: ['text', 'html', 'json-summary'], include: ['src/**/*.{ts,tsx}', '../shared/**/*.{ts,tsx}', '../PWA/src/**/*.{ts,tsx}'], exclude: ['**/main.tsx', '**/vite-env.d.ts'] },
  },
})
