import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': new URL('./src', import.meta.url).pathname,
    },
  },
  server: {
    host: true,
    port: 5173,
    // Vite refuses a `Host` header it does not recognise — a DNS-rebinding
    // guard that is right for a dev server on a laptop and wrong for the one
    // case this repo ships: `make dev` behind a reverse proxy, where the
    // request arrives with the real hostname and the browser is told the host
    // "is not allowed" with no hint that the overlay is what changed.
    //
    // Comma-separated, and empty by default: naming the host is a decision the
    // person running it makes, not one this file makes for every checkout.
    allowedHosts: (process.env.WEB_ALLOWED_HOSTS ?? '')
      .split(',')
      .map((host) => host.trim())
      .filter(Boolean),
    // KloudChat's own API. LiteLLM is never proxied to the browser — the backend
    // holds the master key and calls the proxy server-side.
    proxy: {
      '/api': {
        target: process.env.API_BASE_URL ?? 'http://localhost:8100',
        changeOrigin: true,
      },
    },
  },
})
