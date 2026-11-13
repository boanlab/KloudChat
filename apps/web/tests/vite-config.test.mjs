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

/** A project with a stand-in pptx-react-viewer: its building blocks chunk and its sheet. */
async function crvFixture(t, chunk, sheet) {
  const temporary = await realpath(await mkdtemp(join(tmpdir(), 'kloudchat-crv-')))
  t.after(() => rm(temporary, { recursive: true, force: true }))
  const root = join(temporary, 'web')
  const dist = join(root, 'node_modules/pptx-react-viewer/dist')
  await mkdir(join(root, 'src'), { recursive: true })
  await mkdir(dist, { recursive: true })
  await copyFile(join(webRoot, 'vite.config.ts'), join(root, 'vite.config.ts'))
  await writeFile(join(root, 'package.json'), JSON.stringify({ type: 'module' }))
  for (const dependency of ['vite', '@vitejs/plugin-react', '@tailwindcss/vite']) {
    const target = join(root, 'node_modules', dependency)
    await mkdir(dirname(target), { recursive: true })
    await symlink(join(webRoot, 'node_modules', dependency), target, 'junction')
  }
  await writeFile(join(root, 'node_modules/pptx-react-viewer/package.json'), JSON.stringify({
    name: 'pptx-react-viewer', type: 'module', exports: { '.': './dist/index.mjs', './styles.css': './dist/pptx-viewer.css' },
  }))
  await writeFile(join(dist, 'index.mjs'), "export { useViewerBuildingBlocks } from './chunk-TEST.mjs'")
  await writeFile(join(dist, 'chunk-TEST.mjs'), chunk)
  await writeFile(join(dist, 'pptx-viewer.css'), sheet)
  await writeFile(join(root, 'index.html'), '<script type="module" src="/src/main.js"></script>')
  await writeFile(join(root, 'src/main.js'), [
    "import { useViewerBuildingBlocks } from 'pptx-react-viewer'",
    "import 'pptx-react-viewer/styles.css'",
    'console.log(useViewerBuildingBlocks({}))',
  ].join('\n'))
  return root
}

const BLOCKS = (result) => [
  'export function useViewerBuildingBlocks(input) {',
  '  const state = { setIsDirty: (v) => v }',
  '  const propertyHandlers = { handleUpdateNotes: () => 1 }',
  '  return {',
  result,
  '    collaboration: null',
  '  }',
  '}',
].join('\n')
const RESULT = '    slidesPaneProps,\n    mode,\n    loading,\n    error,\n    autosaveStatus,'
const SHEET = ':root,:host{--font-sans:x;--color-accent:red}@layer utilities{.text-sm{font-size:var(--text-sm)}@media (hover:hover){.p\\:x::placeholder{color:red}}}.flex{display:flex}'

test('the PowerPoint editor gets its notes writer and save mark, and its theme stays in its own box', async (t) => {
  const root = await crvFixture(t, `const slidesPaneProps = 1, mode = 2, loading = 3, error = 4, autosaveStatus = 5\n${BLOCKS(RESULT)}`, SHEET)
  const result = await build({ root, configFile: join(root, 'vite.config.ts'), logLevel: 'silent', build: { write: false, minify: false } })
  const outputs = Array.isArray(result) ? result.flatMap((bundle) => bundle.output) : result.output
  const code = outputs.filter((o) => o.type === 'chunk').map((o) => o.code).join('\n')
  // the bundler may inline the stand-ins; the two members are there
  assert.match(code, /updateNotes:/)
  assert.match(code, /markClean: \(\) =>/)
  const css = outputs.filter((o) => o.type === 'asset' && o.fileName.endsWith('.css'))
    .map((o) => String(o.source)).join('\n')
  assert.ok(!css.includes(':root'), css)
  assert.match(css, /\.kc-slides,\s*\[data-pptx-viewer\],\s*\[data-pptx-scope\]\s*\{/)
  // its utility classes share the app's names, so they apply inside the editor only (and on its
  // own boxes), with no specificity added
  const where = String.raw`:where\(\.kc-slides,\s*\[data-pptx-viewer\],\s*\[data-pptx-scope\]\)`
  assert.match(css, new RegExp(String.raw`${where}\s+\.text-sm\s*,\s*\.text-sm${where}\s*\{`))
  assert.match(css, new RegExp(String.raw`${where}\s+\.p\\:x::placeholder\s*,\s*\.p\\:x${where}::placeholder`))
  // and nowhere unscoped
  assert.equal(css.split('.text-sm').length - 1, 2, css)
})

test('a PowerPoint editor stylesheet without its utilities block fails the build', async (t) => {
  const chunk = `const slidesPaneProps = 1, mode = 2, loading = 3, error = 4, autosaveStatus = 5\n${BLOCKS(RESULT)}`
  const root = await crvFixture(t, chunk, ':root,:host{--font-sans:x}.flex{display:flex}')
  await assert.rejects(
    build({ root, configFile: join(root, 'vite.config.ts'), logLevel: 'silent', build: { write: false } }),
    /has no @layer utilities/,
  )
})

test('a PowerPoint editor version that moved the patched spot fails the build', async (t) => {
  const moved = `const slidesPaneProps = 1, mode = 2\n${BLOCKS('    slidesPaneProps,\n    mode,')}`
  const root = await crvFixture(t, moved, SHEET)
  await assert.rejects(
    build({ root, configFile: join(root, 'vite.config.ts'), logLevel: 'silent', build: { write: false } }),
    /useViewerBuildingBlocks result moved/,
  )
})
