import { defineConfig } from 'vitest/config'

// Separate from vite.config.ts on purpose: tests need none of the dev-server, SSL or service-worker plugins.
export default defineConfig({
  resolve: { dedupe: ['react', 'react-dom'] },
  test: { include: ['tests/**/*.test.ts'], environment: 'node' },
})
