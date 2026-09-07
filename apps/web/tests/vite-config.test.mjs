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
