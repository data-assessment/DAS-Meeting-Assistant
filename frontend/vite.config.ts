import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // Development only. Installed clients use the local server selected by their profile.
  const port = loadEnv(mode, '.', 'VITE_').VITE_TRANSCRIBER_PORT || '8766'
  return {
    plugins: [react()],
    base: './',
    build: { outDir: 'dist', emptyOutDir: true },
    server: {
      port: 5173,
      proxy: {
        '/ws': { target: `ws://127.0.0.1:${port}`, ws: true },
        '/api': { target: `http://127.0.0.1:${port}` },
      },
    },
  }
})
