import { existsSync, readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * The real editor, self-hosted at /rhwp/ under its own CSP: it opens the server's HWPX,
 * draws a page, and what it saves is HWPX again. Runs where `rhwp/build-studio.sh dist/rhwp`
 * built the studio into the served bundle (the HWP job in CI); skipped elsewhere.
 */

test.use({ serviceWorkers: 'block' })

const built = existsSync(new URL('../dist/rhwp/index.html', import.meta.url))
const sessionId = '99999999999999999999999999999999'
const artifactId = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const at = '2026-11-11T00:00:00.000Z'
const hwpx = readFileSync(new URL('./fixtures/hwp-official.hwpx', import.meta.url))

async function fixture(page: Page) {
  const violations: string[] = []
  const saves: Buffer[] = []
  page.on('console', (message) => {
    if (/Content Security Policy|Refused to/i.test(message.text())) violations.push(message.text())
  })
  let artifact = {
    id: artifactId, title: '직원 연수 참석 요청', kind: 'hwp', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { format: 'hwpx', size: hwpx.length, text: '' },
  }
  const session = {
    id: sessionId, title: '연수 공문', kind: 'chat', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pinned: false, messages: [],
    messageCount: 0, made: null, createdAt: at, updatedAt: at,
  }
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const method = request.method()
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'fixture-user', email: 'fixture@example.test', name: 'HWP', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
        allowedModels: [], createdAt: at,
        preferences: { autoMemory: false, showUsage: false, streamResponses: true },
      },
    })
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    })
    if (path === '/models') return json({ models: [], defaultChatModel: '', litellmAvailable: false,
      autoRouting: { enabled: false, available: false } })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && method === 'GET') return json([session])
    if (path === `/sessions/${sessionId}`) return json(session)
    if (path === '/artifacts' && method === 'GET') return json([artifact])
    if (path === `/artifacts/${artifactId}`) return json(artifact)
    if (path === `/artifacts/${artifactId}/hwp` && method === 'GET') {
      return route.fulfill({ body: hwpx, contentType: 'application/hwp+zip',
        headers: { 'X-Artifact-Version': String(artifact.version) } })
    }
    if (path === `/artifacts/${artifactId}/hwp` && method === 'PUT') {
      saves.push(request.postDataBuffer() ?? Buffer.alloc(0))
      artifact = { ...artifact, version: artifact.version + 1 }
      return json(artifact)
    }
    if (path === '/artifacts/counts') return json({ counts: { hwp: 1 }, total: 1 })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    return json([])
  })
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  return { violations, saves }
}

test('the self-hosted editor draws the document and saves HWPX back', async ({ page }) => {
  test.skip(!built, 'rhwp-studio not built into dist/rhwp')
  test.setTimeout(120_000)
  const state = await fixture(page)
  const frame = page.frameLocator('[data-hwp-panel] iframe')
  // a drawn page: the studio paints pages on canvases
  await expect.poll(async () => frame.locator('canvas').evaluateAll(
    (nodes) => nodes.filter((n) => (n as HTMLCanvasElement).width > 200).length,
  ), { timeout: 60_000 }).toBeGreaterThan(0)
  // the notice goes once the editor says it opened, which can trail the first drawn page
  await expect(page.getByText('문서를 여는 중…')).toBeHidden({ timeout: 15_000 })

  // the frame is ours: same origin, its own policy with WebAssembly allowed
  const response = await page.request.get('/rhwp/')
  const csp = response.headers()['content-security-policy']
  expect(csp).toContain("'wasm-unsafe-eval'")
  expect(csp).not.toContain('https:')

  await frame.locator('#scroll-container').click({ position: { x: 300, y: 330 } })
  await page.keyboard.type('추가 문장')
  const header = page.locator('[data-panel="artifact"] header')
  await expect(header.getByText('저장 안 됨')).toBeVisible({ timeout: 10_000 })
  await header.getByRole('button', { name: '저장' }).click()
  await expect(header.getByText('v2', { exact: true })).toBeVisible()
  expect(state.saves).toHaveLength(1)
  expect(state.saves[0].subarray(0, 4).toString('latin1')).toBe('PK\u0003\u0004')
  expect(state.violations).toEqual([])
})
