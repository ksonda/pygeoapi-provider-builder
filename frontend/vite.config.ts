import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend binds to 127.0.0.1:8765 (python -m provider_builder); the dev
// server proxies /api to it so the browser sees a single origin.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } },
  },
  build: { outDir: 'dist', sourcemap: true },
})
