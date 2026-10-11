import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 60000,
  use: { browserName: 'chromium', viewport: { width: 1440, height: 960 }, trace: 'retain-on-failure' },
  webServer: [
    { command: 'uv run --locked --directory ../backend python -m tests.browser_server', url: 'http://127.0.0.1:18980/health', timeout: 30000 },
    { command: 'uv run --locked --directory ../backend python -m http.server 18981 --bind 127.0.0.1 --directory ../frontend/dist', url: 'http://127.0.0.1:18981' },
    { command: 'uv run --locked --directory ../backend python -m http.server 18982 --bind 127.0.0.1 --directory ../PWA/dist', url: 'http://127.0.0.1:18982' },
  ],
})
