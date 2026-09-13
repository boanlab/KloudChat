import { expect, test, type Download, type Page, type TestInfo } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

test.use({ serviceWorkers: 'block' })

const sessionId = '11111111111111111111111111111111'
const artifactId = '22222222222222222222222222222222'
const at = '2026-09-14T00:00:00.000Z'
const source = 'day,activity\n화요일,복습\n목요일,문제풀이\n'

async function fixture(page: Page, testInfo: TestInfo, options: {
  kind?: 'code' | 'html'; language?: string; title?: string; filename?: string; fail?: boolean
} = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  expect(['localhost', '127.0.0.1']).toContain(new URL(origin).hostname)
  const unexpected: string[] = []
  const exports: { format: string | null; authorization: string | undefined }[] = []
  const kind = options.kind ?? 'code'
  const title = options.title ?? '주간 일정'
  const content = kind === 'html' ? '<h1>스터디 일정</h1><p>화요일 복습</p>' : source
  const artifact = { id: artifactId, title, kind, version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { kind, content, ...(options.language ? { language: options.language } : {}) } }
  const session = { id: sessionId, title, kind: 'chat', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId, pinned: false, messages: [], messageCount: 0,
    made: null, createdAt: at, updatedAt: at }
  await page.context().routeWebSocket('**/*', (socket) => { unexpected.push('WebSocket'); socket.close() })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) {
      unexpected.push(`external ${url.origin}`)
      return route.abort('blockedbyclient')
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: 'Download fixture', email: 'fixture@example.test', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
        createdAt: at, preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'fixture/base', label: 'Fixture',
      name: 'Fixture', vendor: 'Fixture', provider: 'fixture', kinds: ['chat'], modality: 'chat',
      dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/base', litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [session] })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: [artifact] })
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return route.fulfill({ json: artifact })
    if (method === 'GET' && path === `/artifacts/${artifactId}/export`) {
      exports.push({ format: url.searchParams.get('format'), authorization: request.headers().authorization })
      if (options.fail && exports.length === 1) return route.fulfill({ status: 404, json: { detail: 'Artifact not found' } })
      return route.fulfill({ body: content, headers: {
        'Content-Type': kind === 'html' ? 'text/html' : options.language === 'csv' ? 'text/csv' : 'text/plain',
        ...(options.filename ? { 'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(options.filename)}` } : {}),
      } })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: { [kind]: 1 }, total: 1 } })
    if (method === 'GET' && [`/sessions/${sessionId}/messages`, `/sessions/${sessionId}/jobs`, '/projects',
      '/skills', '/memory', '/agents', '/tools', '/templates', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/design-templates', '/prompt-templates', '/shares'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.abort('blockedbyclient')
  })
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  return { exports, unexpected, content }
}

async function downloadedText(download: Download) {
  const path = await download.path()
  expect(path).not.toBeNull()
  return readFile(path!, 'utf8')
}

for (const width of [1440, 390]) {
  test(`CSV source downloads through the authenticated export API at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
    const state = await fixture(page, testInfo, { language: 'csv', title: '주간 일정.csv', filename: '주간 일정.csv' })
    await page.reload()
    const button = page.getByRole('button', { name: '원본 다운로드', exact: true })
    await expect(button).toBeVisible()
    await expect(page.locator('pre').filter({ hasText: source.trim() })).toBeVisible()
    const pending = page.waitForEvent('download')
    await button.click()
    const download = await pending
    expect(download.suggestedFilename()).toBe('주간 일정.csv')
    expect(await downloadedText(download)).toBe(source)
    expect(state.exports).toEqual([{ format: 'source', authorization: 'Bearer fixture-only' }])
    await expect(button).toBeEnabled()
    await expect(page.getByRole('button', { name: '내보내기', exact: true })).toHaveCount(0)
    const bounds = await button.boundingBox()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width)
    const screenshot = testInfo.outputPath(`artifact-source-${width}.png`)
    await page.screenshot({ path: screenshot })
    await testInfo.attach('source-download', { path: screenshot, contentType: 'image/png' })
    if (process.env.QA_SCREENSHOT_DIR) {
      await mkdir(process.env.QA_SCREENSHOT_DIR, { recursive: true })
      await page.screenshot({ path: join(process.env.QA_SCREENSHOT_DIR, `artifact-source-${width}.png`) })
    }
    expect(state.unexpected).toEqual([])
  })
}

for (const language of ['text', 'unknown', undefined]) {
  test(`a ${language ?? 'missing'} language uses the server txt filename without CSV inference`, async ({ page }, testInfo) => {
    const state = await fixture(page, testInfo, { language, filename: '주간 일정.txt' })
    const pending = page.waitForEvent('download')
    await page.getByRole('button', { name: '원본 다운로드', exact: true }).click()
    const download = await pending
    expect(download.suggestedFilename()).toBe('주간 일정.txt')
    expect(await downloadedText(download)).toBe(source)
    expect(state.exports).toHaveLength(1)
    expect(state.unexpected).toEqual([])
  })
}

test('a missing download filename falls back to text, not a source extension', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const pending = page.waitForEvent('download')
  await page.getByRole('button', { name: '원본 다운로드', exact: true }).click()
  expect((await pending).suggestedFilename()).toBe('주간 일정.txt')
  expect(state.unexpected).toEqual([])
})

test('an owner-scoped export failure shows an error and permits retry without a local fallback', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo, { language: 'csv', filename: '주간 일정.csv', fail: true })
  const downloads: Download[] = []
  page.on('download', (download) => downloads.push(download))
  const button = page.getByRole('button', { name: '원본 다운로드', exact: true })
  await button.click()
  await expect(page.getByRole('alert')).toContainText('Artifact not found')
  expect(downloads).toEqual([])
  await expect(button).toBeEnabled()
  const pending = page.waitForEvent('download')
  await button.click()
  await pending
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(state.exports).toHaveLength(2)
  expect(state.unexpected).toEqual([])
})

test('HTML keeps its preview, source tabs and existing export formats', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo, { kind: 'html', language: 'html' })
  await expect(page.getByRole('button', { name: '원본 다운로드', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '소스', exact: true }).click()
  await expect(page.locator('pre')).toHaveText(state.content)
  await page.getByRole('button', { name: '미리보기', exact: true }).click()
  await expect(page.locator('iframe').first()).toBeVisible()
  await page.getByRole('button', { name: '내보내기', exact: true }).click()
  const pending = page.waitForEvent('download')
  await page.getByRole('menuitem').filter({ hasText: 'HTML' }).click()
  const download = await pending
  expect(download.suggestedFilename()).toBe('주간 일정.html')
  expect(await downloadedText(download)).toBe(state.content)
  expect(state.exports).toEqual([{ format: 'html', authorization: 'Bearer fixture-only' }])
  expect(state.unexpected).toEqual([])
})
