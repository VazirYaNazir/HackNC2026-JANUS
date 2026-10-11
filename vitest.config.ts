import { defineConfig } from 'vitest/config';

export default defineConfig({
  oxc: { jsx: { runtime: 'automatic' } },
  resolve: { dedupe: ['react', 'react-dom'] },
  test: {
    include: ['frontend/tests/**/*.test.{ts,tsx}', 'PWA/tests/**/*.test.{ts,tsx}', 'scripts/tests/**/*.test.mjs'],
    coverage: { provider: 'v8', reporter: ['text', 'html', 'json-summary'], include: ['frontend/src/**/*.{ts,tsx}', 'PWA/src/**/*.{ts,tsx}', 'shared/**/*.{ts,tsx}'], exclude: ['**/main.tsx'], thresholds: { statements: 80, branches: 70, functions: 75, lines: 85 } },
  },
});
