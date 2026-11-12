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
function securityHeaders(name = './security-headers.conf'): Record<string, string> {
  const file = fileURLToPath(new URL(name, import.meta.url))
  if (!existsSync(file)) return {}
  const lines = readFileSync(file, 'utf8').matchAll(/^add_header ([\w-]+) "([^"]*)" always;$/gm)
  return Object.fromEntries([...lines].map((line) => [line[1], line[2]]))
}

/** `/rhwp/` (the HWP editor) under its own headers, as nginx serves it: the app's would
 *  block its WebAssembly. Preview only; the dev server sets no headers. */
const rhwpHeaders = (): Plugin => ({
  name: 'kchat-rhwp-headers',
  configurePreviewServer(server) {
    const own = securityHeaders('./rhwp-security-headers.conf')
    const byName = new Map(Object.entries(own).map(([name, value]) => [name.toLowerCase(), value]))
    server.middlewares.use((req, res, next) => {
      if (req.url?.startsWith('/rhwp/')) {
        // preview sets the app's headers after this runs: the editor's win by name
        const setHeader = res.setHeader.bind(res)
        res.setHeader = (name, value) => setHeader(name, byName.get(name.toLowerCase()) ?? value)
        for (const [name, value] of Object.entries(own)) setHeader(name, value)
      }
      next()
    })
  },
})

/**
 * Build-time changes to the PowerPoint editor (pptx-react-viewer, `components/pptx`), pinned to
 * its exact version in package.json; node_modules is untouched.
 * - `useViewerBuildingBlocks()` hands out neither the speaker-notes writer its own
 *   `PowerPointViewer` uses nor a way to clear its unsaved flag after a save: both are added to
 *   its result (`updateNotes`, `markClean`).
 * - Its stylesheet sets its theme variables (`--font-sans`, `--color-accent`, …) on `:root`,
 *   where they would replace the app's own once a deck opens: they are set on the editor's
 *   own elements instead.
 * A new version that moves either spot fails the build rather than shipping without them.
 */
const CRV_RESULT = '    slidesPaneProps,\n    mode,\n    loading,\n    error,\n    autosaveStatus,'
const CRV_ADDED =
  '    updateNotes: propertyHandlers.handleUpdateNotes,\n' +
  '    markClean: () => state.setIsDirty(false),\n'
const CRV_ROOT = ':root,:host{'
const CRV_SCOPE = '.kc-slides,[data-pptx-viewer],[data-pptx-scope]{'

function crvPatches(): Plugin {
  let building = false
  const seen = { script: false, sheet: false }
  const done = { script: false, sheet: false }
  return {
    name: 'kchat-crv-patches',
    enforce: 'pre',
    configResolved(config) {
      building = config.command === 'build'
    },
    transform(code, id) {
      if (!id.includes('/pptx-react-viewer/dist/')) return null
      if (/\/chunk-[^/]*\.m?js$/.test(id) && code.includes('function useViewerBuildingBlocks(')) {
        seen.script = true
        if (!code.includes(CRV_RESULT)) return null
        done.script = true
        return { code: code.replace(CRV_RESULT, CRV_ADDED + CRV_RESULT), map: null }
      }
      if (/\/pptx-viewer\.css(\?|$)/.test(id)) {
        seen.sheet = true
        if (!code.includes(CRV_ROOT)) return null
        done.sheet = true
        return { code: code.split(CRV_ROOT).join(CRV_SCOPE), map: null }
      }
      return null
    },
    buildEnd(error) {
      if (!building || error) return
      if (seen.script && !done.script) {
        this.error('pptx-react-viewer: the useViewerBuildingBlocks result moved; update CRV_RESULT')
      }
      if (seen.sheet && !done.sheet) {
        this.error('pptx-react-viewer: its stylesheet no longer sets :root,:host; update CRV_ROOT')
      }
    },
  }
}

export default defineConfig({
  plugins: [crvPatches(), react(), tailwindcss(), versionFile(), rhwpHeaders()],
  define: {
    'import.meta.env.VITE_KCHAT_VERSION': JSON.stringify(version),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // MPL-2.0, and only for fonts embedded as MTX/EOT in old decks: not shipped, such a font
      // falls back to the deck's other fonts (components/pptx/mtxDecompressor.ts)
      'mtx-decompressor': fileURLToPath(
        new URL('./src/components/pptx/mtxDecompressor.ts', import.meta.url),
      ),
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
