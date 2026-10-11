import basicSsl from '@vitejs/plugin-basic-ssl'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // basicSsl serves the dev server over https; phones only allow the camera on secure origins.
  plugins: [react(), basicSsl()],
  // @janus/design (lucide-react) lives outside this package; keep a single React instance.
  resolve: { dedupe: ['react', 'react-dom'] },
})
