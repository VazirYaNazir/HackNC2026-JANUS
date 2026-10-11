import { readFileSync } from 'node:fs'
import basicSsl from '@vitejs/plugin-basic-ssl'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// The manifest and <meta name="theme-color"> use the design token's value, so there is one source of truth.
const tokens = readFileSync(new URL('../shared/design/tokens.css', import.meta.url), 'utf8')
const surface = /--surface:\s*(#[0-9a-fA-F]{6})/.exec(tokens)?.[1]
if (!surface) throw new Error('--surface not found in shared/design/tokens.css')

/** Fills the __SURFACE__ placeholder in index.html. */
const surfaceColor = (): Plugin => ({
  name: 'janus-surface-color',
  transformIndexHtml: (html) => html.replaceAll('__SURFACE__', surface),
})

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // basicSsl serves the dev server over https; phones only allow the camera on secure origins.
    basicSsl(),
    surfaceColor(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['icon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'JANUS',
        short_name: 'JANUS',
        description: 'Custom control panels for your PC, on your phone.',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait',
        theme_color: surface,
        background_color: surface,
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      // Precache the app shell; unknown paths fall back to index.html like the Vercel rewrite does.
      workbox: { navigateFallback: '/index.html', globPatterns: ['**/*.{js,css,html,svg,png,webmanifest}'] },
    }),
  ],
  // @janus/design (lucide-react) lives outside this package; keep a single React instance.
  resolve: { dedupe: ['react', 'react-dom'] },
})
