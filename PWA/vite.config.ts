import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // @janus/design (lucide-react) lives outside this package; keep a single React instance.
  resolve: { dedupe: ['react', 'react-dom'] },
})
