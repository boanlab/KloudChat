import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'
import deck from './fixtures/presentation-deck.json' with { type: 'json' }

test.use({ serviceWorkers: 'block' })

const sessionId = '11111111111111111111111111111111'
const artifactId = '22222222222222222222222222222222'
const at = '2026-09-08T00:00:00.000Z'
const viewports = [
  { width: 390, height: 844 },
  { width: 844, height: 390 },
  { width: 1440, height: 900 },
]

async function fixture(page: Page, testInfo: TestInfo, html = false) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  expect(['localhost', '127.0.0.1']).toContain(new URL(origin).hostname)
  const unexpected: string[] = []
  await page.context().routeWebSocket('**/*', (socket) => {
    // Vite's same-origin development channel is not an application API.
    const url = new URL(socket.url())
    if (url.origin === origin.replace(/^http/, 'ws') && url.pathname === '/') {
      socket.connectToServer()
      return
    }
    unexpected.push('unexpected WebSocket')
    socket.close()
  })
  const title = html ? 'HTML presentation fixture' : deck.title
  const data = html ? { kind: 'html', content: '<!doctype html><html><head><style>body{margin:0;background:#fff;color:#111;font:16px sans-serif}.slide{box-sizing:border-box;padding:12px}h1{font-size:18px;margin:0}</style></head><body><section class="slide"><h1>A</h1><p>First</p></section><section class="slide"><h1>B</h1><p>Second</p></section></body></html>' } : deck.data
  const artifact = { id: artifactId, title, data, kind: data.kind, version: 1,
    partial: false, sessionId, projectId: null, createdAt: at, updatedAt: at }
  const session = { id: sessionId, title, kind: 'slides', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId, pinned: false, messages: [], messageCount: 0,
    made: null, createdAt: at, updatedAt: at }
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
      user: { id: 'fixture-user', name: 'Presentation fixture', email: 'fixture@example.test', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
        createdAt: at, preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'slides'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'fixture/base', label: 'Fixture',
      name: 'Fixture', vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'slides'], modality: 'chat',
      dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/base', litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [session] })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: [artifact] })
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return route.fulfill({ json: artifact })
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: { [data.kind]: 1 }, total: 1 } })
    if (method === 'GET' && [`/sessions/${sessionId}/messages`, `/sessions/${sessionId}/jobs`, '/projects',
      '/skills', '/memory', '/agents', '/tools', '/templates', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/design-templates', '/prompt-templates', '/shares'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.abort('blockedbyclient')
  })
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  if (!html) await page.getByRole('tab', { name: '슬라이드 쇼', exact: true }).click()
  await page.getByRole('button', { name: '발표', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '발표 모드', exact: true })).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
  return unexpected
}

const dialog = (page: Page) => page.getByRole('dialog', { name: '발표 모드', exact: true })
const button = (page: Page, name: string) => dialog(page).getByRole('button', { name, exact: true })

async function expectControlsFit(page: Page) {
  const fits = await dialog(page).locator('button').evaluateAll((buttons) => buttons.map((element) => {
    const r = element.getBoundingClientRect()
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
    return { label: element.getAttribute('aria-label') ?? element.textContent,
      fits: r.x >= 0 && r.y >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
      hit: hit === element || element.contains(hit) }
  }))
  expect(fits.filter((entry) => !entry.fits || !entry.hit)).toEqual([])
}

async function tableBounds(table: Locator) {
  return table.evaluate((element) => {
    const stage = element.closest('.aspect-video')!
    const r = stage.getBoundingClientRect()
    const parent = stage.parentElement!
    const parentBox = parent.getBoundingClientRect()
    const parentStyle = getComputedStyle(parent)
    const available = {
      width: parentBox.width - parseFloat(parentStyle.paddingLeft) - parseFloat(parentStyle.paddingRight)
        - parseFloat(parentStyle.borderLeftWidth) - parseFloat(parentStyle.borderRightWidth),
      height: parentBox.height - parseFloat(parentStyle.paddingTop) - parseFloat(parentStyle.paddingBottom)
        - parseFloat(parentStyle.borderTopWidth) - parseFloat(parentStyle.borderBottomWidth),
    }
    const maxWidth = parseFloat(getComputedStyle(stage).maxWidth)
    const expectedWidth = Math.min(Number.isFinite(maxWidth) ? maxWidth : Infinity,
      Math.floor(Math.min(available.width, available.height * 16 / 9)))
    const cells = [...element.querySelectorAll('td')].map((cell) => {
      const range = document.createRange()
      range.selectNodeContents(cell)
      let clip = { left: 0, top: 0, right: innerWidth, bottom: innerHeight }
      let parent: HTMLElement | null = cell
      while (parent) {
        const style = getComputedStyle(parent)
        const box = parent.getBoundingClientRect()
        if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowX)) {
          clip.left = Math.max(clip.left, box.left); clip.right = Math.min(clip.right, box.right)
        }
        if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowY)) {
          clip.top = Math.max(clip.top, box.top); clip.bottom = Math.min(clip.bottom, box.bottom)
        }
        parent = parent.parentElement
      }
      const rectangles = [...range.getClientRects()].map((box) => ({ left: box.left, top: box.top,
        right: box.right, bottom: box.bottom }))
      return { text: cell.textContent, fontPx: parseFloat(getComputedStyle(cell).fontSize), clip, rectangles,
        clipped: rectangles.some((box) => box.left < clip.left - 1 || box.top < clip.top - 1
          || box.right > clip.right + 1 || box.bottom > clip.bottom + 1) }
    })
    return { width: r.width, height: r.height, left: r.left, top: r.top, right: r.right, bottom: r.bottom,
      available, expectedWidth,
      viewport: { width: innerWidth, height: innerHeight }, cells }
  })
}

async function expectTableFits(page: Page, testInfo: TestInfo, label: string) {
  const table = dialog(page).locator('table')
  // typeScale.tableSize(3) is TYPE.tableMax (16pt) / K (2.4); this fixture has no textScale.
  const fontUnits = 16 / 2.4
  await expect(table.locator('td')).toHaveText(deck.data.slides[2].rows!.flat())
  await testInfo.attach(`${label}-before-assertions`, {
    body: JSON.stringify(await tableBounds(table), null, 2), contentType: 'application/json',
  })
  await expect.poll(async () => {
    const bounds = await tableBounds(table)
    const expectedFontPx = fontUnits * bounds.width / 400
    return {
      aspectFits: Math.abs(bounds.width - bounds.height * 16 / 9) <= 1,
      widthSettled: Math.abs(bounds.width - bounds.expectedWidth) <= 1,
      scaleSettled: bounds.cells.every((cell) => Math.abs(cell.fontPx - expectedFontPx) <= 0.01),
      clipped: bounds.cells.filter((cell) => cell.clipped).map((cell) => cell.text),
    }
  }).toEqual({ aspectFits: true, widthSettled: true, scaleSettled: true, clipped: [] })
  const bounds = await tableBounds(table)
  const expectedFontPx = fontUnits * bounds.width / 400
  expect(bounds.cells.every((cell) => Math.abs(cell.fontPx - expectedFontPx) <= 0.01)).toBe(true)
  expect(Math.abs(bounds.width - bounds.expectedWidth)).toBeLessThanOrEqual(1)
  expect(bounds.width).toBeGreaterThan(0)
  expect(bounds.left).toBeGreaterThanOrEqual(0)
  expect(bounds.top).toBeGreaterThanOrEqual(0)
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewport.width)
  expect(bounds.bottom).toBeLessThanOrEqual(bounds.viewport.height)
  await testInfo.attach(`${label}-settled`, {
    body: JSON.stringify({ ...bounds, expectedFontPx }, null, 2), contentType: 'application/json',
  })
  await expectControlsFit(page)
}

for (const notes of [true, false]) {
  for (const outline of [true, false]) {
    test(`JSON presentation fits table with notes=${notes}, outline=${outline}`, async ({ page }, testInfo) => {
      const unexpected = await fixture(page, testInfo)
      await button(page, '다음 장').click()
      await button(page, '다음 장').click()
      await expect(dialog(page).getByRole('heading', { name: '담당자 역할 비교' })).toBeVisible()
      if (!notes) {
        await button(page, '노트 (N)').click()
        await expect(dialog(page).getByText(deck.data.slides[2].notes!, { exact: true })).toHaveCount(0)
      }
      if (outline) {
        await button(page, '장 목록').click()
        await expect(dialog(page).getByRole('navigation', { name: '장 목록', exact: true })).toBeVisible()
      }
      await expectTableFits(page, testInfo, 'overlay')
      await button(page, '전체 화면').click()
      await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true)
      await expect(button(page, '전체 화면 끝내기')).toHaveAttribute('aria-pressed', 'true')
      await expectTableFits(page, testInfo, 'fullscreen')
      await button(page, '전체 화면 끝내기').click()
      await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(false)
      if (outline) {
        const narrowWidth = (await tableBounds(dialog(page).locator('table'))).width
        await button(page, '장 목록').click()
        await expect(dialog(page).getByRole('navigation', { name: '장 목록', exact: true })).toHaveCount(0)
        await expectTableFits(page, testInfo, 'outline-closed')
        expect((await tableBounds(dialog(page).locator('table'))).width).toBeGreaterThanOrEqual(narrowWidth)
      }
      await button(page, '발표 끝내기').click()
      await expect(dialog(page)).toHaveCount(0)
      expect(unexpected).toEqual([])
    })
  }
}

test('JSON navigation and rotation retain the fitted slide without mutating its data', async ({ page }, testInfo) => {
  const unexpected = await fixture(page, testInfo)
  await expect(button(page, '이전 장')).toBeDisabled()
  await button(page, '다음 장').click()
  await expect(dialog(page).getByRole('heading', { name: '신청 진행 단계' })).toBeVisible()
  await button(page, '이전 장').click()
  await expect(dialog(page).getByRole('heading', { name: deck.title })).toBeVisible()
  await button(page, '장 목록').click()
  await button(page, '3번 장').click()
  await expect(button(page, '3번 장')).toHaveAttribute('aria-current', 'true')
  await button(page, '장 목록').click()
  await expect(dialog(page).getByRole('navigation', { name: '장 목록', exact: true })).toHaveCount(0)
  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await expectTableFits(page, testInfo, `rotated-${viewport.width}`)
  }
  await button(page, '다음 장').click()
  await expect(dialog(page).getByRole('heading', { name: '요약' })).toBeVisible()
  await expect(button(page, '다음 장')).toBeDisabled()
  await page.keyboard.press('ArrowLeft')
  await expectTableFits(page, testInfo, 'keyboard-previous')
  await page.keyboard.press('n')
  await expect(dialog(page).getByText(deck.data.slides[2].notes!, { exact: true })).toHaveCount(0)
  await page.keyboard.press('n')
  await expect(dialog(page).getByText(deck.data.slides[2].notes!, { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog(page)).toHaveCount(0)
  await expect(page.getByRole('tab', { name: '슬라이드 쇼', exact: true })).toBeVisible()
  expect(unexpected).toEqual([])
})

test('HTML presentation keeps its sandboxed frame and shared controls', async ({ page }, testInfo) => {
  const unexpected = await fixture(page, testInfo, true)
  const frame = () => dialog(page).frameLocator('iframe')
  await expect(dialog(page).locator('iframe')).toHaveAttribute('sandbox', 'allow-scripts')
  await expect(frame().locator('section.slide')).toHaveCount(1)
  await expect(frame().getByRole('heading', { name: 'A', exact: true })).toBeVisible()
  await expect(frame().getByRole('heading', { name: 'A', exact: true })).toBeInViewport()
  await expect(button(page, '이전 장')).toBeDisabled()
  await expect(button(page, '노트 (N)')).toHaveCount(0)
  await expectControlsFit(page)
  await button(page, '다음 장').click()
  await expect(frame().getByRole('heading', { name: 'B', exact: true })).toBeVisible()
  await expect(button(page, '다음 장')).toBeDisabled()
  await button(page, '장 목록').click()
  await button(page, '1번 장').click()
  await button(page, '장 목록').click()
  await expect(frame().getByRole('heading', { name: 'A', exact: true })).toBeVisible()
  await button(page, '전체 화면').click()
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true)
  await expect(frame().getByText('First', { exact: true })).toBeVisible()
  await expect(frame().getByText('First', { exact: true })).toBeInViewport()
  await expectControlsFit(page)
  await button(page, '전체 화면 끝내기').click()
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(false)
  await button(page, '발표 끝내기').click()
  await expect(dialog(page)).toHaveCount(0)
  expect(unexpected).toEqual([])
})
