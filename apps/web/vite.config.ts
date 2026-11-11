import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, relative } from 'node:path'
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

/** The Word editor's document fonts (`@stll/folio-fonts/fonts.css`, which its stylesheet
 *  imports) cut to what this app reads: the latin and latin-ext faces of its metric
 *  stand-ins for Calibri, Arial, Times and the rest, as woff2 only; the cyrillic, greek,
 *  hebrew and vietnamese cuts, the Arabic family and the `.woff` copies for headless
 *  measuring are not shipped. Korean serif text (바탕, 궁서) falls back to `Noto Serif KR` in the editor; that
 *  name is given the app's own Nanum Myeongjo cuts, so no other Korean files ship. */
const folioFonts = (): Plugin => ({
  name: 'kchat-folio-fonts',
  enforce: 'pre',
  transform(code, id) {
    const sheet = id.split('?')[0]
    if (!sheet.replaceAll('\\', '/').endsWith('/@stll/folio-react/dist/editor.css')) return null
    const require = createRequire(sheet)
    const faces = (file: string) => {
      // each face is written as `url(./…)` beside its own sheet; here they move into this one
      const from = relative(dirname(sheet), dirname(file)).replaceAll('\\', '/')
      const css = readFileSync(file, 'utf8')
      return (css.match(/\/\* [\w[\]-]{1,80} \*\/\s{0,4}@font-face \{[^}]{0,2000}\}/g) ?? [])
        .map((face) => face
          .replace(/(format\('woff2'\)),[^;]{0,400};/, '$1;')
          .replaceAll('url(./', `url(./${from}/`))
    }
    const latin = faces(require.resolve('@stll/folio-fonts/fonts.css')).filter((face) =>
      /^\/\* (?!noto-sans-arabic)[a-z0-9-]{1,40}-latin(?:-ext)?-\d00-(?:normal|italic) \*\//
        .test(face))
    const serif = ['400', '700'].flatMap((weight) =>
      faces(require.resolve(`@fontsource/nanum-myeongjo/${weight}.css`)).map((face) =>
        face.replace("font-family: 'Nanum Myeongjo'", "font-family: 'Noto Serif KR'")))
    const imported = '@import "@stll/folio-fonts/fonts.css";'
    if (!code.includes(imported)) this.error(`${sheet} no longer imports its fonts as expected`)
    return code.replace(imported, [...latin, ...serif].join('\n'))
  },
})

export default defineConfig({
  plugins: [react(), tailwindcss(), versionFile(), rhwpHeaders(), folioFonts()],
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
