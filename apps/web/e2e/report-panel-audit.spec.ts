import { expect, test, type Page, type TestInfo } from '@playwright/test'
import audit from './fixtures/report-audit.json' with { type: 'json' }
import style from './fixtures/report-audit-style.json' with { type: 'json' }

test.use({ serviceWorkers: 'block' })

const sessionId = '55555555555555555555555555555555'
const artifactId = '66666666666666666666666666666666'
const at = '2026-10-05T00:00:00.000Z'

type Options = { failWrites?: boolean; data?: Record<string, unknown> }

async function fixture(page: Page, testInfo: TestInfo, options: Options = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const problems: string[] = []
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error') problems.push(`console: ${message.text().slice(0, 300)}`)
  })
  let artifact = {
    id: artifactId, title: audit.title, kind: 'report', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { ...audit.data, kind: 'report', title: audit.title, ...(options.data ?? {}) },
  }
  const session = { id: sessionId, title: artifact.title, kind: 'report', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId, pinned: false, messages: [], messageCount: 0,
    made: null, createdAt: at, updatedAt: at }
  await page.context().routeWebSocket('**/*', (socket) => { unexpected.push('WebSocket'); socket.close() })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'GET' && /^\/design-templates\/[a-z-]+\/style$/.test(path)) return route.fulfill({ json: style })
    if (method === 'GET' && path === '/design-templates') return route.fulfill({ json: audit.templates })
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: 'Report fixture', email: 'fixture@example.test', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
        createdAt: at, preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'fixture/base', label: 'Fixture',
      name: 'Fixture', vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report'], modality: 'chat',
      dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/base', litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [session] })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: [artifact] })
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return route.fulfill({ json: artifact })
    if (method === 'GET' && path === `/artifacts/${artifactId}/versions`) return route.fulfill({ json: [] })
    if (method === 'PATCH' && path === `/artifacts/${artifactId}`) {
      if (options.failWrites) return route.fulfill({ status: 500, json: { detail: 'internal_error' } })
      const patch = request.postDataJSON()
      artifact = { ...artifact, title: patch.title ?? artifact.title, data: patch.data ?? artifact.data, version: artifact.version + 1 }
      return route.fulfill({ json: artifact })
    }
    if (method === 'POST' && path.startsWith(`/artifacts/${artifactId}/`)) {
      if (options.failWrites) return route.fulfill({ status: 500, json: { detail: 'internal_error' } })
      return route.fulfill({ json: artifact })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: { report: 1 }, total: 1 } })
    if (method === 'GET' && [`/sessions/${sessionId}/messages`, `/sessions/${sessionId}/jobs`, '/projects',
      '/skills', '/memory', '/agents', '/tools', '/templates', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/prompt-templates', '/shares'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  await expect(page.locator('[data-panel="artifact"]')).toBeVisible()
  return { unexpected, problems }
}

const panel = (page: Page) => page.locator('[data-panel="artifact"]')
const tab = (page: Page, name: string) => panel(page).getByRole('tab', { name, exact: true })
const TABS = ['홈', '편집', '삽입', '레이아웃', '검토', '보기', '파일']

/** What is open over the document: menus, dialogs, popovers. */
async function openLayers(page: Page) {
  return page.locator('[role="menu"]:visible, [role="dialog"]:visible, [role="listbox"]:visible').count()
}

test('walkthrough: every tab and control, with screenshots', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  const state = await fixture(page, testInfo)
  const shots = testInfo.outputPath('shots')
  const notes: string[] = []
  let n = 0
  const shot = async (name: string) => {
    n += 1
    await page.screenshot({ path: `${shots}/${String(n).padStart(2, '0')}-${name}.png` })
  }
  await shot('opened')
  for (const name of TABS) {
    await tab(page, name).click()
    await page.waitForTimeout(500)
    await shot(`tab-${name}`)
    // Every enabled button in the ribbon, one at a time.
    const ribbon = panel(page).locator('header')
    const labels = await ribbon.locator('button:visible:not([disabled])').evaluateAll((buttons) =>
      buttons.map((b) => (b.getAttribute('aria-label') || b.textContent || '').trim()).filter(Boolean))
    for (const label of labels) {
      if (TABS.includes(label) || /닫기|Close|패널|확대|넓게|좁게/.test(label)) continue
      const button = ribbon.getByRole('button', { name: label, exact: true }).first()
      if (!(await button.isVisible().catch(() => false))) continue
      await button.click({ timeout: 3000 }).catch((e) => notes.push(`click failed ${name}/${label}: ${String(e).slice(0, 80)}`))
      await page.waitForTimeout(400)
      await shot(`${name}-${label.replace(/[^\p{L}\p{N}]+/gu, '_').slice(0, 24)}`)
      const layers = await openLayers(page)
      await page.keyboard.press('Escape')
      await page.waitForTimeout(200)
      const after = await openLayers(page)
      if (after > 0) notes.push(`${name}/${label}: ${after} layer(s) still open after Escape (had ${layers})`)
      // Every other tab: nothing this tab opened may stay.
    }
  }
  const found = [...notes, ...state.problems, ...state.unexpected.map((u) => `unexpected ${u}`)]
  await testInfo.attach('notes', { body: found.join('\n') })
  // A click that fails, a layer Escape leaves open, a page error or an unplanned request
  // fails the walkthrough; it is not only written down.
  expect(found).toEqual([])
})

/** Soft checks: every failure is collected, the run goes on, and the list fails at the end. */
function checker() {
  const failures: string[] = []
  return {
    failures,
    async check(name: string, condition: () => Promise<boolean>) {
      const ok = await condition().catch(() => false)
      if (!ok) failures.push(name)
    },
  }
}

const webView = (page: Page) => panel(page).locator('article.doc-web')
const pagedView = (page: Page) => panel(page).locator('.pagedjs_page').first()
const homeButton = (page: Page, name: string) => panel(page).locator('header').getByRole('button', { name, exact: true })

test('flows: views round-trip and every panel belongs to its tab', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  const state = await fixture(page, testInfo)
  const { failures, check } = checker()
  const shots = testInfo.outputPath('flows')
  let n = 0
  const shot = async (name: string) => { n += 1; await page.screenshot({ path: `${shots}/${String(n).padStart(2, '0')}-${name}.png` }) }

  // Web → page → web, three times over different routes.
  await homeButton(page, '페이지뷰').click()
  await check('page view shows pages', async () => { await expect(pagedView(page)).toBeVisible({ timeout: 30_000 }); return true })
  await homeButton(page, '웹뷰').click()
  await check('web view returns after page view', async () => { await expect(webView(page)).toBeVisible({ timeout: 5000 }); return true })
  await shot('back-to-web-1')
  await page.waitForTimeout(1500)
  await check('view buttons are enabled and opaque after returning', async () => {
    const states = await panel(page).locator('header').getByRole('button', { name: /^(웹뷰|페이지뷰)$/ }).evaluateAll((buttons) =>
      buttons.map((b) => ({ disabled: (b as HTMLButtonElement).disabled, opacity: Number(getComputedStyle(b).opacity) })))
    return states.length === 2 && states.every((x) => !x.disabled && x.opacity > 0.95)
  })
  await shot('back-to-web-settled')

  await tab(page, '편집').click()
  await page.waitForTimeout(800)
  await shot('edit-tab')
  await tab(page, '홈').click()
  await page.waitForTimeout(500)
  await check('leaving 편집 restores the web view it came from', async () => { await expect(webView(page)).toBeVisible({ timeout: 5000 }); return true })
  await homeButton(page, '페이지뷰').click()
  await page.waitForTimeout(500)
  await homeButton(page, '편집').click().catch(() => {})
  await page.waitForTimeout(500)
  await homeButton(page, '웹뷰').click()
  await check('web view returns from the page editor', async () => { await expect(webView(page)).toBeVisible({ timeout: 5000 }); return true })
  await shot('back-to-web-2')

  await tab(page, '레이아웃').click()
  await homeButton(page, '페이지 설정').click()
  await page.waitForTimeout(500)
  await tab(page, '홈').click()
  await homeButton(page, '웹뷰').click()
  await check('web view returns after page settings', async () => { await expect(webView(page)).toBeVisible({ timeout: 5000 }); return true })
  await shot('back-to-web-3')

  // A panel opened from a tab closes when another tab is chosen.
  const settingsField = panel(page).getByText('머리말', { exact: true })
  await tab(page, '레이아웃').click()
  await homeButton(page, '페이지 설정').click()
  for (const other of ['홈', '편집', '삽입', '검토', '보기', '파일']) {
    await tab(page, other).click()
    await page.waitForTimeout(300)
    await check(`page settings hidden on ${other}`, async () => !(await settingsField.isVisible()))
  }
  await tab(page, '레이아웃').click()
  await page.waitForTimeout(300)
  await check('page settings closed when coming back to 레이아웃', async () => !(await settingsField.isVisible()))

  await tab(page, '검토').click()
  await panel(page).locator('header').getByRole('button', { name: /출처/ }).click()
  await page.waitForTimeout(400)
  const sourcesPane = panel(page).getByRole('heading', { name: '참고문헌' })
  await check('sources pane opens from 검토', async () => sourcesPane.isVisible())
  for (const other of ['홈', '삽입', '레이아웃', '보기', '파일']) {
    await tab(page, other).click()
    await page.waitForTimeout(300)
    await check(`sources pane closed on ${other}`, async () => !(await sourcesPane.isVisible()))
  }
  await tab(page, '보기').click()
  await panel(page).locator('header').getByRole('button', { name: /목차/ }).click()
  await page.waitForTimeout(400)
  await shot('toc-open')
  await check('table of contents lists every section, below the ribbon', async () => {
    const names = audit.data.sections.map((sec: { heading: string }) => sec.heading)
    for (const name of names) {
      const item = panel(page).locator('nav').getByRole('button', { name: new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }).first()
      const box = await item.boundingBox(); const ribbon = await panel(page).locator('header').boundingBox()
      if (!box || !ribbon || box.y < ribbon.y + ribbon.height - 1) return false
    }
    return true
  })
  await check('ribbon stays usable with the table of contents open', async () => {
    await tab(page, '홈').click({ timeout: 2000 }); await tab(page, '보기').click({ timeout: 2000 })
    return true
  })
  await panel(page).locator('header').getByRole('button', { name: /목차/ }).click()
  await page.waitForTimeout(300)
  const toc = panel(page).locator('nav, aside').filter({ hasText: '시장 및 도입 동향' }).first()
  for (const other of ['홈', '파일']) {
    await tab(page, other).click()
    await page.waitForTimeout(300)
    await check(`table of contents closed on ${other}`, async () => !(await toc.isVisible()))
    await shot(`toc-after-${other}`)
  }
  // Menus close when their tab is left.
  await tab(page, '홈').click()
  await panel(page).locator('header').getByRole('button', { name: /강조색/ }).click()
  await tab(page, '파일').click()
  await page.waitForTimeout(300)
  await check('accent menu closes on tab change', async () => (await openLayers(page)) === 0)
  await page.keyboard.press('Escape')

  // The 그림 넣기 menu opens inside the panel.
  await tab(page, '삽입').click()
  await panel(page).locator('header').getByRole('button', { name: /그림 넣기/ }).click()
  const menu = page.locator('[role="menu"]:visible').first()
  await check('그림 넣기 menu sits inside the panel', async () => {
    const box = await menu.boundingBox(); const area = await panel(page).boundingBox()
    return Boolean(box && area && box.x >= area.x - 1)
  })
  await shot('insert-menu')
  await page.keyboard.press('Escape')

  console.log('FLOW FAILURES\n' + failures.join('\n'))
  console.log('PROBLEMS\n' + [...state.problems, ...state.unexpected].join('\n'))
  expect(failures).toEqual([])
})

test('errors: every failure says so in place, aligned, and can be left', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  await page.addInitScript(() => { (window as Window & { __KLOUDCHAT_FORCE_PAGINATION_FAILURE__?: boolean }).__KLOUDCHAT_FORCE_PAGINATION_FAILURE__ = true })
  const state = await fixture(page, testInfo, { failWrites: true })
  const { failures, check } = checker()
  const shots = testInfo.outputPath('errors')
  let n = 0
  const shot = async (name: string) => { n += 1; await page.screenshot({ path: `${shots}/${String(n).padStart(2, '0')}-${name}.png` }) }

  // Pagination failure.
  await homeButton(page, '페이지뷰').click()
  const failCard = panel(page).getByText(/페이지를 나누지 못했습니다/)
  await check('pagination failure is shown', async () => { await expect(failCard).toBeVisible({ timeout: 15_000 }); return true })
  await shot('pagination-failed')
  await check('pagination failure offers the web view', async () => {
    await panel(page).getByRole('button', { name: /웹뷰/ }).last().click(); await expect(webView(page)).toBeVisible({ timeout: 5000 }); return true
  })
  // Save failure: change the accent while writes fail.
  await panel(page).locator('header').getByRole('button', { name: /강조색/ }).click()
  await page.locator('[role="menu"]:visible').getByText('파랑', { exact: true }).click()
  const saveError = panel(page).locator('[role="alert"] p').first()
  await check('save failure is shown', async () => { await expect(saveError).toBeVisible({ timeout: 10_000 }); return true })
  await page.waitForTimeout(300)
  await shot('save-failed')
  await check('save failure banner has even vertical padding', async () => {
    const box = await saveError.evaluate((el) => {
      const card = el.closest('[role="alert"]') as HTMLElement
      const outer = card.getBoundingClientRect(); const inner = el.getBoundingClientRect()
      return { top: inner.top - outer.top, bottom: outer.bottom - inner.bottom }
    })
    return Math.abs(box.top - box.bottom) <= 3
  })
  // Template change failure.
  await panel(page).locator('header').getByRole('button', { name: /양식/ }).click()
  await page.locator('[role="menu"]:visible').getByText('사업 제안서').click()
  await page.waitForTimeout(800)
  await shot('template-failed')
  await check('template failure keeps the old template name', async () =>
    (await panel(page).locator('header').getByRole('button', { name: /양식/ }).textContent())?.includes('표준 보고서') ?? false)
  // Export failure (the mock answers 404).
  await tab(page, '파일').click()
  await panel(page).locator('header').getByRole('button', { name: /내보내기/ }).click()
  await page.locator('[role="menu"]:visible').getByText('Word 문서').click()
  await page.waitForTimeout(1200)
  await shot('export-failed')
  await check('export failure is shown, not an earlier message', async () => {
    const text = await panel(page).locator('[role="alert"]').first().textContent()
    return Boolean(text && !text.includes('서식을 바꾸지'))
  })
  await check('error banner can be dismissed', async () => {
    await panel(page).locator('[role="alert"]').getByRole('button', { name: '닫기' }).click()
    return !(await panel(page).locator('[role="alert"]').first().isVisible())
  })
  await page.screenshot({ path: `${shots}/after-dismiss.png` })

  console.log('ERROR FAILURES\n' + failures.join('\n'))
  console.log('PROBLEMS\n' + [...state.problems, ...state.unexpected].join('\n'))
  expect(failures).toEqual([])
})

test('flows: a report with a template opens in page view and still returns to web view', async ({ page }, testInfo) => {
  test.setTimeout(180_000)
  const state = await fixture(page, testInfo, { data: { templateId: 'doc-proposal' } })
  const { failures, check } = checker()
  const shots = testInfo.outputPath('templated')
  await check('opens in page view', async () => { await expect(pagedView(page)).toBeVisible({ timeout: 30_000 }); return true })
  for (let round = 1; round <= 3; round += 1) {
    await homeButton(page, '웹뷰').click()
    await page.waitForTimeout(1500)
    await page.screenshot({ path: `${shots}/round-${round}-web.png` })
    await check(`round ${round}: web view shown and stays`, async () => {
      await expect(webView(page)).toBeVisible({ timeout: 5000 })
      await page.waitForTimeout(1500)
      return webView(page).isVisible()
    })
    await homeButton(page, '페이지뷰').click()
    await check(`round ${round}: page view shown`, async () => { await expect(pagedView(page)).toBeVisible({ timeout: 30_000 }); return true })
  }
  // Panel size changes must not reset the view.
  await homeButton(page, '웹뷰').click()
  const resize = panel(page).getByRole('button', { name: /문서만 보기|넓게|좁게|패널/ }).first()
  if (await resize.isVisible().catch(() => false)) {
    await resize.click(); await page.waitForTimeout(800)
    await check('web view survives a panel size change', async () => webView(page).isVisible())
  }
  console.log('TEMPLATED FAILURES\n' + failures.join('\n'))
  console.log('PROBLEMS\n' + [...state.problems, ...state.unexpected].join('\n'))
  expect(failures).toEqual([])
})

test('flows: the source editor gives way to the views', async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  await fixture(page, testInfo)
  const { failures, check } = checker()
  await homeButton(page, '원문 편집').click()
  await expect(panel(page).locator('textarea').first()).toBeVisible()
  await homeButton(page, '페이지뷰').click()
  await check('page view replaces the source editor', async () => { await expect(pagedView(page)).toBeVisible({ timeout: 30_000 }); return true })
  await homeButton(page, '웹뷰').click()
  await check('web view after source editor', async () => { await expect(webView(page)).toBeVisible({ timeout: 5000 }); return true })
  await page.screenshot({ path: testInfo.outputPath('source-after.png') })
  console.log('SOURCE FAILURES\n' + failures.join('\n'))
  expect(failures).toEqual([])
})

test('errors: every section action that fails says so', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  const state = await fixture(page, testInfo, { failWrites: true })
  const { failures, check } = checker()
  const shots = testInfo.outputPath('section-errors')
  const first = audit.data.sections[0].heading as string
  const menuButton = panel(page).getByRole('button', { name: `${first} 절 편집` })
  const items = ['앞에 절 추가', '뒤에 절 추가', '아래로 옮기기', '이 절 지우기', '이 절만 다시 쓰기', '검토']
  let n = 0
  for (const item of items) {
    await menuButton.click()
    const entry = page.locator('[role="menu"]:visible').getByRole('menuitem', { name: item === '검토' ? /^(다시 )?검토$/ : new RegExp(item) }).first()
    if (!(await entry.isVisible().catch(() => false))) { failures.push(`menu item missing: ${item}`); await page.keyboard.press('Escape'); continue }
    await entry.click()
    await page.waitForTimeout(600)
    // A rewrite asks for a note first; send it.
    const send = page.getByRole('button', { name: /다시 쓰기|보내기|확인/ }).filter({ hasNot: page.locator('[role="menu"]') }).last()
    if (item === '이 절만 다시 쓰기' && (await send.isVisible().catch(() => false))) { await send.click(); await page.waitForTimeout(600) }
    // A delete asks first; confirm it.
    const confirm = page.getByRole('dialog').getByRole('button', { name: /지우기|삭제/ }).last()
    if (await confirm.isVisible().catch(() => false)) { await confirm.click(); await page.waitForTimeout(600) }
    n += 1
    await page.screenshot({ path: `${shots}/${String(n).padStart(2, '0')}-${item}.png` })
    await check(`${item}: failure is shown`, async () => panel(page).locator('[role="alert"], .text-danger').first().isVisible())
    await check(`${item}: document still there`, async () => panel(page).getByRole('heading', { name: first }).first().isVisible())
    const close = panel(page).locator('[role="alert"]').getByRole('button', { name: '닫기' })
    if (await close.isVisible().catch(() => false)) await close.click()
    await page.keyboard.press('Escape')
  }
  console.log('SECTION FAILURES\n' + failures.join('\n'))
  console.log('PROBLEMS\n' + [...state.problems.filter((x) => !x.includes('Failed to load resource')), ...state.unexpected].join('\n'))
  expect(failures).toEqual([])
})

test.describe('phone width', () => {
  test.use({ viewport: { width: 400, height: 860 } })
  test('the panel at phone width: every tab fits and nothing spills', async ({ page }, testInfo) => {
    test.setTimeout(180_000)
    const state = await fixture(page, testInfo)
    const { failures, check } = checker()
    const shots = testInfo.outputPath('phone')
    for (const name of TABS) {
      await tab(page, name).click()
      await page.waitForTimeout(500)
      await page.screenshot({ path: `${shots}/${name}.png` })
      await check(`${name}: no horizontal page scroll`, async () =>
        page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
      await check(`${name}: ribbon controls inside the screen`, async () => {
        const boxes = await panel(page).locator('header button:visible:not([role="tab"])').evaluateAll((bs) =>
          bs.map((b) => ({ label: (b.getAttribute('aria-label') || b.textContent || '').trim().slice(0, 20), right: Math.round(b.getBoundingClientRect().right) })))
        const out = boxes.filter((b) => b.right > 401)
        if (out.length) console.log(`${name} outside: ${JSON.stringify(out)}`)
        return out.length === 0
      })
    }
    console.log('PHONE FAILURES\n' + failures.join('\n'))
    console.log('PROBLEMS\n' + [...state.problems, ...state.unexpected].join('\n'))
    expect(failures).toEqual([])
  })
})
