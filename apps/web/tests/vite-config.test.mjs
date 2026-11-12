import assert from 'node:assert/strict'
import { copyFile, mkdir, mkdtemp, realpath, rm, symlink, writeFile } from 'node:fs/promises'
import net from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { build, loadConfigFromFile } from 'vite'

const webRoot = fileURLToPath(new URL('../', import.meta.url))
const directories = [
  ['ASCII', 'workspace'],
  ['spaces', 'workspace with spaces'],
  ['Unicode', '\ud55c\uae00\uacbd\ub85c'],
  ['Unicode and spaces', '\ud55c\uae00 \uacbd\ub85c'],
]

for (const [label, directory] of directories) {
  test(`Vite resolves source aliases in ${label} workspace paths`, async (t) => {
    const noNetwork = () => assert.fail('Vite config tests must not access the network')
    t.mock.method(net.Socket.prototype, 'connect', noNetwork)
    t.mock.method(globalThis, 'fetch', noNetwork)

    const temporary = await realpath(await mkdtemp(join(tmpdir(), 'kloudchat-vite-path-')))
    t.after(() => rm(temporary, { recursive: true, force: true }))
    const root = join(temporary, directory)
    await mkdir(join(root, 'src'), { recursive: true })
    const configFile = join(root, 'vite.config.ts')
    await copyFile(join(webRoot, 'vite.config.ts'), configFile)
    await writeFile(join(root, 'package.json'), JSON.stringify({ type: 'module' }))

    // Reuse installed packages, but keep Vite's temporary config bundle in this fixture.
    for (const dependency of ['vite', '@vitejs/plugin-react', '@tailwindcss/vite']) {
      const target = join(root, 'node_modules', dependency)
      await mkdir(dirname(target), { recursive: true })
      await symlink(join(webRoot, 'node_modules', dependency), target, 'junction')
    }
    await writeFile(join(root, 'index.html'), '<script type="module" src="/src/main.js"></script>')
    await writeFile(
      join(root, 'src/main.js'),
      "import { message } from '@/message.js'; console.log(message)",
    )
    await writeFile(join(root, 'src/message.js'), "export const message = 'alias path verified'")

    const loaded = await loadConfigFromFile({ command: 'build', mode: 'production' }, configFile)
    assert.ok(loaded)
    assert.equal(loaded.config.resolve.alias['@'], join(root, 'src'))

    const result = await build({ root, configFile, logLevel: 'silent', build: { write: false } })
    const outputs = Array.isArray(result) ? result.flatMap((bundle) => bundle.output) : result.output
    assert.ok(outputs.some(
      (output) => output.type === 'chunk' && output.code.includes('alias path verified'),
    ))
  })
}

test('the HWP editor at /rhwp/ gets its own policy and the app keeps its own', async () => {
  const { readFile } = await import('node:fs/promises')
  const policy = async (name) => {
    const text = await readFile(join(webRoot, name), 'utf8')
    return text.match(/^add_header Content-Security-Policy "([^"]*)" always;$/m)[1]
  }
  const app = await policy('security-headers.conf')
  const editor = await policy('rhwp-security-headers.conf')
  const scripts = (csp) => csp.split(';').map((part) => part.trim())
    .find((part) => part.startsWith('script-src'))
  // the editor compiles WebAssembly; the app never needs to
  assert.ok(!app.includes('wasm-unsafe-eval'))
  assert.equal(scripts(editor), "script-src 'self' 'wasm-unsafe-eval'")
  // nothing the editor shows or sends leaves this site
  assert.ok(!/https:|\*/.test(editor))
  assert.ok(editor.includes("frame-ancestors 'self'"))
  // the app may frame it
  assert.match(app, /frame-src 'self'/)

  const nginx = await readFile(join(webRoot, 'nginx.conf'), 'utf8')
  const block = nginx.slice(nginx.indexOf('location ^~ /rhwp/'))
  assert.match(block, /include \/etc\/nginx\/snippets\/rhwp-security-headers\.conf;/)
  const dockerfile = await readFile(join(webRoot, 'Dockerfile'), 'utf8')
  assert.match(dockerfile, /COPY rhwp-security-headers\.conf \/etc\/nginx\/snippets\//)
})

test('the Word editor ships its metric fonts as woff2 latin cuts beside its sheet', async () => {
  const { readFile } = await import('node:fs/promises')
  const { existsSync } = await import('node:fs')
  const { createRequire } = await import('node:module')
  const sheet = createRequire(join(webRoot, 'package.json')).resolve('open-docx-editor/fonts.css')
  const css = await readFile(sheet, 'utf8')
  const fonts = [...css.matchAll(/url\('([^']+)'\)/g)].map((m) => m[1])
  assert.ok(fonts.length > 0)
  assert.ok(fonts.every((url) => url.endsWith('.woff2')))
  for (const url of fonts) assert.ok(existsSync(join(dirname(sheet), url)), url)
  assert.ok(!/cyrillic|greek|hebrew|vietnamese|arabic|cjk|korean/.test(fonts.join(' ')))
  // Korean serif is the app's own Nanum Myeongjo (components/docx/koreanFonts.ts)
  const source = await readFile(join(webRoot, 'src/components/docx/DocxPanel.tsx'), 'utf8')
  assert.match(source, /import 'open-docx-editor\/fonts\.css'/)
  assert.match(source, /aliasKoreanFonts\(\)/)
})
