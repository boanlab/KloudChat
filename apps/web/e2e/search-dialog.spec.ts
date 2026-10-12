import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { conversationRows, hubFixture } from './customize-fixture'
import { viewport } from './project'

/**
 * ⌘K search: one dialog over conversations and results, by title and by what they say. The
 * sidebar's 검색 box opens the same dialog, and 대화 기록 searches through the same endpoint.
 * Fixture data only; `/api/search` is answered here from the rows below.
 */

test.use({ serviceWorkers: 'block' })

const SHOTS = process.env.SEARCH_SHOTS

async function shot(page: Page, name: string, testInfo: TestInfo) {
  if (!SHOTS) return
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/${name}-${viewport(testInfo)}.png` })
}

const rows = conversationRows(30).map((row, i) => ({
  ...row,
  kind: i === 2 ? 'report' : 'chat',
  title: i === 2 ? '2027 예산 편성 보고서' : i === 5 ? '예산 회의 준비' : row.title,
  pinned: false,
}))
const artifactId = 'f1111111111111111111111111111111'

type Run = { text: string; hit: boolean }
const marked = (text: string, word: string): Run[] => {
  const at = text.indexOf(word)
  if (at < 0) return [{ text, hit: false }]
  return [
    ...(at > 0 ? [{ text: text.slice(0, at), hit: false }] : []),
    { text: word, hit: true },
    ...(at + word.length < text.length ? [{ text: text.slice(at + word.length), hit: false }] : []),
  ]
}

/** Every conversation says the word in a message; two also carry it in their titles. */
function answer(q: string, scope: string, limit: number, offset: number) {
  const word = q.trim().split(/\s+/)[0] ?? ''
  const matching = word === '예산' ? rows : []
  const ordered = [...matching].sort((a, b) => Number(b.title.includes(word)) - Number(a.title.includes(word)))
  const sessions = ordered.map((row) => ({
    id: row.id, kind: row.kind, title: row.title, titleParts: marked(row.title, word),
    matchedIn: row.title.includes(word) ? 'title' : 'message',
    snippet: row.title.includes(word) ? [] : marked(`…지난주에 정한 ${word} 한도를 넘지 않게 정리했습니다…`, word),
    messageId: `m-${row.id}`, role: 'assistant', projectId: null, artifactId: null, updatedAt: row.updatedAt,
  }))
  const artifacts = word === '예산'
    ? [{ id: artifactId, kind: 'report', title: '2027 예산 편성안', titleParts: marked('2027 예산 편성안', word),
        matchedIn: 'title', snippet: marked('부서별 예산 증감과 근거를 정리한 보고서', word), sessionId: rows[2].id,
        projectId: null, updatedAt: rows[2].updatedAt }]
    : []
  const page = <T,>(list: T[]) => list.slice(offset, offset + limit)
  return {
    query: q, terms: word ? [word] : [],
    sessions: scope === 'artifacts' ? [] : page(sessions),
    artifacts: scope === 'sessions' ? [] : page(artifacts),
    sessionsMore: scope !== 'artifacts' && sessions.length > offset + limit,
    artifactsMore: scope !== 'sessions' && artifacts.length > offset + limit,
    sessionsTotal: sessions.length,
  }
}

async function searchFixture(page: Page, testInfo: TestInfo) {
  const state = await hubFixture(page, testInfo, { conversations: 0 })
  const searches: URLSearchParams[] = []
  // Page routes run before the fixture's context route.
  await page.route('**/api/sessions', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: rows }) : route.fallback())
  await page.route(/\/api\/sessions\/[^/?]+(\/[a-z]+)?(\?.*)?$/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const path = new URL(route.request().url()).pathname
    if (/\/(messages|jobs)$/.test(path)) return route.fulfill({ json: [] })
    const row = rows.find((r) => path.endsWith(r.id))
    return row ? route.fulfill({ json: { ...row, messages: [] } }) : route.fallback()
  })
  await page.route('**/api/search?*', (route) => {
    const params = new URL(route.request().url()).searchParams
    searches.push(params)
    return route.fulfill({ json: answer(params.get('q') ?? '', params.get('scope') ?? 'all',
      Number(params.get('limit') ?? 8), Number(params.get('offset') ?? 0)) })
  })
  return { ...state, searches }
}

const dialog = (page: Page) => page.getByRole('dialog', { name: '검색' })
const box = (page: Page) => dialog(page).getByRole('combobox', { name: '대화와 아티팩트 검색' })
const options = (page: Page) => dialog(page).getByRole('option')

test('⌘K opens the dialog: recent conversations, then grouped, marked results by content', async ({ page }, testInfo) => {
  const state = await searchFixture(page, testInfo)
  await page.goto('/customize')
  await expect(page.getByRole('heading', { level: 1, name: '사용자 지정' })).toBeVisible({ timeout: 15_000 })

  await page.keyboard.press('ControlOrMeta+k')
  await expect(dialog(page)).toBeVisible()
  await expect(box(page)).toBeFocused()
  // Nothing typed: the newest conversations, no request.
  await expect(dialog(page).getByText('최근 대화')).toBeVisible()
  await expect(options(page)).toHaveCount(8)
  await expect(options(page).first()).toContainText(rows[0].title)
  expect(state.searches).toHaveLength(0)
  await shot(page, 'search-empty', testInfo)

  await box(page).fill('예산')
  const conversations = dialog(page).getByRole('region', { name: '대화' })
  const results = dialog(page).getByRole('region', { name: '아티팩트' })
  await expect(conversations).toBeVisible()
  await expect(results).toBeVisible()
  // Title matches first, the word marked; then message matches with a marked window.
  await expect(conversations.getByRole('option').first()).toContainText('2027 예산 편성 보고서')
  await expect(conversations.getByRole('option').first().locator('mark')).toHaveText('예산')
  const byMessage = conversations.getByRole('option').nth(2)
  await expect(byMessage.locator('[data-snippet]')).toContainText('한도를 넘지 않게')
  await expect(byMessage.locator('[data-snippet] mark')).toHaveText('예산')
  await expect(results.getByRole('option')).toContainText('2027 예산 편성안')
  await expect(dialog(page).getByRole('option', { name: '대화 더 보기' })).toBeVisible()
  expect(state.searches.at(-1)?.get('q')).toBe('예산')
  await shot(page, 'search-results', testInfo)

  // More of one group, in place.
  await dialog(page).getByRole('option', { name: '대화 더 보기' }).click()
  await expect(conversations.getByRole('option').filter({ hasNot: page.getByText('대화 더 보기') })).toHaveCount(16)
  expect(state.searches.at(-1)?.get('scope')).toBe('sessions')
  expect(state.searches.at(-1)?.get('offset')).toBe('8')

  // Arrows move the selection; Enter opens it.
  await options(page).first().hover()
  await box(page).focus()
  await expect(options(page).first()).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(options(page).nth(1)).toHaveAttribute('aria-selected', 'true')
  await expect(box(page)).toHaveAttribute('aria-activedescendant', await options(page).nth(1).getAttribute('id') ?? '')
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('ArrowUp')
  // Wraps to the last row: the result.
  await expect(options(page).last()).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(new RegExp(`/s/${rows[2].id}\\?artifact=${artifactId}$`))
  await expect(dialog(page)).toHaveCount(0)

  // A conversation by Enter, and Esc closes.
  await page.keyboard.press('ControlOrMeta+k')
  await box(page).fill('예산')
  await expect(dialog(page).getByRole('region', { name: '대화' })).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(new RegExp(`/s/${rows[2].id}$`))
  await page.keyboard.press('ControlOrMeta+k')
  await expect(dialog(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog(page)).toHaveCount(0)

  await page.keyboard.press('ControlOrMeta+k')
  await box(page).fill('없는말')
  await expect(dialog(page).getByText('「없는말」와 맞는 대화나 아티팩트가 없습니다')).toBeVisible()
  expect(state.problems).toEqual([])
})

test('the sidebar 검색 box opens the same dialog', { tag: '@phone' }, async ({ page }, testInfo) => {
  const state = await searchFixture(page, testInfo)
  await page.goto('/customize')
  await expect(page.getByRole('heading', { level: 1, name: '사용자 지정' })).toBeVisible({ timeout: 15_000 })
  if (viewport(testInfo) === 'phone') await page.getByRole('button', { name: '사이드바 토글' }).click()
  await page.locator('aside').getByRole('button', { name: '대화 빠른 검색' }).click()
  await expect(dialog(page)).toBeVisible()
  await expect(box(page)).toBeFocused()
  await box(page).fill('예산')
  await expect(dialog(page).getByRole('region', { name: '대화' })).toBeVisible()
  await dialog(page).getByRole('option').filter({ hasText: '예산 회의 준비' }).click()
  await expect(page).toHaveURL(new RegExp(`/s/${rows[5].id}$`))
  expect(state.problems).toEqual([])
})

test('대화 기록 searches titles and messages through the same endpoint and keeps ?q=', async ({ page }, testInfo) => {
  const state = await searchFixture(page, testInfo)
  await page.goto('/history?q=%EC%98%88%EC%82%B0')
  await expect(page.getByRole('heading', { level: 1, name: '대화 기록' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByLabel('대화 검색')).toHaveValue('예산')
  await expect(page.locator('[data-snippet]').first()).toContainText('한도를 넘지 않게')
  expect(state.searches.at(-1)?.get('scope')).toBe('sessions')
  await shot(page, 'history-search', testInfo)
  await page.getByLabel('대화 검색').fill('없는말')
  await expect(page).toHaveURL(/q=%EC%97%86%EB%8A%94%EB%A7%90/)
  await expect(page.getByText('검색 결과가 없습니다')).toBeVisible()
  await page.getByLabel('대화 검색').fill('')
  await expect(page).toHaveURL(/\/history$/)
  expect(state.problems).toEqual([])
})

/** 대화 기록 a page at a time: 50 compact rows, the page and the words in the address. */

const pagedRows = conversationRows(120).map((row) => ({ ...row, pinned: false }))

async function historyFixture(page: Page, testInfo: TestInfo) {
  const state = await hubFixture(page, testInfo, { conversations: 0 })
  const asked: URLSearchParams[] = []
  await page.route('**/api/sessions/paged?*', (route) => {
    const params = new URL(route.request().url()).searchParams
    asked.push(params)
    const size = Number(params.get('size') ?? 50)
    const last = Math.max(1, Math.ceil(pagedRows.length / size))
    const at = Math.min(Number(params.get('page') ?? 1), last)
    return route.fulfill({ json: { items: pagedRows.slice((at - 1) * size, at * size), total: pagedRows.length, page: at, size } })
  })
  await page.route('**/api/search?*', (route) => {
    const params = new URL(route.request().url()).searchParams
    asked.push(params)
    const offset = Number(params.get('offset') ?? 0)
    const limit = Number(params.get('limit') ?? 50)
    const hits = pagedRows.filter((_, i) => i % 2 === 0).map((row) => ({
      id: row.id, kind: 'chat', title: row.title, titleParts: [{ text: row.title, hit: false }], matchedIn: 'message',
      snippet: [{ text: '…올해 ', hit: false }, { text: '예산', hit: true }, { text: ' 이야기…', hit: false }],
      messageId: null, role: 'user', projectId: null, artifactId: null, updatedAt: row.updatedAt,
    }))
    return route.fulfill({ json: { query: params.get('q'), terms: [params.get('q')], sessions: hits.slice(offset, offset + limit),
      artifacts: [], sessionsMore: hits.length > offset + limit, artifactsMore: false,
      sessionsTotal: params.get('count') === 'true' ? hits.length : null } })
  })
  return { ...state, asked }
}

const historyRows = (page: Page) => page.locator('[data-history-row]')
const pager = (page: Page) => page.getByRole('navigation', { name: '페이지 이동', exact: true })

test('50 compact rows a page; page and words live in the address', { tag: '@phone' }, async ({ page }, testInfo) => {
  const state = await historyFixture(page, testInfo)
  await page.goto('/history')
  await expect(page.getByRole('heading', { level: 1, name: '대화 기록' })).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('[data-history-total]')).toContainText('대화 120개가 있습니다')
  await expect(historyRows(page)).toHaveCount(50)
  await expect(page.locator('[data-history-range]')).toHaveText('120개 중 1–50')
  await expect(pager(page).getByRole('button', { name: '1쪽' })).toHaveAttribute('aria-current', 'page')
  // One line per conversation (the phone's larger type and touch target make it a little taller).
  const box = await historyRows(page).first().boundingBox()
  expect(box!.height).toBeLessThanOrEqual(viewport(testInfo) === 'phone' ? 64 : 48)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/history-paged-${viewport(testInfo)}.png` })

  await pager(page).getByRole('button', { name: '2쪽' }).click()
  await expect(page).toHaveURL(/\/history\?page=2$/)
  await expect(page.locator('[data-history-range]')).toHaveText('120개 중 51–100')
  await expect(historyRows(page).first()).toContainText(pagedRows[50].title)

  await pager(page).getByRole('button', { name: '마지막 페이지' }).click()
  await expect(page).toHaveURL(/page=3$/)
  await expect(historyRows(page)).toHaveCount(20)
  await expect(pager(page).getByRole('button', { name: '다음 페이지' })).toBeDisabled()

  // Selecting is per page.
  await page.getByRole('button', { name: '이 페이지 전체 선택' }).click()
  await expect(page.getByRole('button', { name: '선택 20개 삭제' })).toBeEnabled()
  await page.getByRole('button', { name: '선택 해제' }).click()

  // The compact pager above the list, and the browser's back button.
  await page.getByRole('navigation', { name: '페이지 이동(위)' }).getByRole('button', { name: '이전 페이지' }).click()
  await expect(page).toHaveURL(/page=2$/)
  await page.goBack()
  await expect(page).toHaveURL(/page=3$/)
  await expect(historyRows(page)).toHaveCount(20)

  // A reload keeps the page; one past the end lands on the last.
  await page.goto('/history?page=9')
  await expect(page).toHaveURL(/page=3$/)
  await expect(historyRows(page)).toHaveCount(20)

  // Words start again on page 1 and page through the search.
  await page.getByLabel('대화 검색').fill('예산')
  await expect(page).toHaveURL(/\/history\?q=%EC%98%88%EC%82%B0$/)
  await expect(page.locator('[data-history-range]')).toHaveText('「예산」 검색 결과 60개 중 1–50')
  await expect(page.locator('[data-snippet] mark').first()).toHaveText('예산')
  await pager(page).getByRole('button', { name: '2쪽' }).click()
  await expect(page).toHaveURL(/q=%EC%98%88%EC%82%B0&page=2$/)
  await expect(historyRows(page)).toHaveCount(10)
  const search = state.asked.filter((p) => p.has('q')).at(-1)!
  expect([search.get('offset'), search.get('limit'), search.get('count')]).toEqual(['50', '50', 'true'])
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/history-search-paged-${viewport(testInfo)}.png` })
  expect(state.problems).toEqual([])
})
