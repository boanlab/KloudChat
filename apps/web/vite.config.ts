import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

/** Names this build; `/version.json` says the same, so an open page can tell a deploy happened. */
const version = process.env.KCHAT_BUILD_VERSION || Date.now().toString(36)

/** Emits `/version.json` beside the bundle. */
const versionFile = (): Plugin => ({
  name: 'kchat-version',
  generateBundle() {
    this.emitFile({ type: 'asset', fileName: 'version.json', source: JSON.stringify({ version }) })
  },
})

/** nginx's shared headers (`security-headers.conf`), served by `vite preview` as well. */
function securityHeaders(): Record<string, string> {
  const file = fileURLToPath(new URL('./security-headers.conf', import.meta.url))
  if (!existsSync(file)) return {}
  const lines = readFileSync(file, 'utf8').matchAll(/^add_header ([\w-]+) "([^"]*)" always;$/gm)
  return Object.fromEntries([...lines].map((line) => [line[1], line[2]]))
}

export default defineConfig({
  plugins: [react(), tailwindcss(), versionFile()],
  define: {
    'import.meta.env.VITE_KCHAT_VERSION': JSON.stringify(version),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: true,
    port: 5173,
    // Vite rejects unknown `Host` headers; behind a reverse proxy the real
    // hostname must be listed here. Comma-separated, empty by default.
    allowedHosts: (process.env.WEB_ALLOWED_HOSTS ?? '')
      .split(',')
      .map((host) => host.trim())
      .filter(Boolean),
    // The app's own API only. LiteLLM is never proxied to the browser; the
    // backend holds the master key.
    proxy: {
      '/api': {
        target: process.env.API_BASE_URL ?? 'http://localhost:8100',
        changeOrigin: true,
      },
    },
  },
  // The production headers, so the e2e suite (served by `vite preview`) runs under the CSP.
  preview: {
    headers: securityHeaders(),
  },
})
