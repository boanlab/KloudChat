import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { conversationRows, hubFixture } from './customize-fixture'

/** 대화 기록 a page at a time: 50 compact rows, the page and the words in the address. */

test.use({ serviceWorkers: 'block' })

const SHOTS = process.env.SEARCH_SHOTS
const rows = conversationRows(120).map((row) => ({ ...row, pinned: false }))

async function historyFixture(page: Page, testInfo: TestInfo) {
  const state = await hubFixture(page, testInfo, { conversations: 0 })
  const asked: URLSearchParams[] = []
  await page.route('**/api/sessions/paged?*', (route) => {
    const params = new URL(route.request().url()).searchParams
    asked.push(params)
    const size = Number(params.get('size') ?? 50)
    const last = Math.max(1, Math.ceil(rows.length / size))
    const at = Math.min(Number(params.get('page') ?? 1), last)
    return route.fulfill({ json: { items: rows.slice((at - 1) * size, at * size), total: rows.length, page: at, size } })
  })
  await page.route('**/api/search?*', (route) => {
    const params = new URL(route.request().url()).searchParams
    asked.push(params)
    const offset = Number(params.get('offset') ?? 0)
    const limit = Number(params.get('limit') ?? 50)
    const hits = rows.filter((_, i) => i % 2 === 0).map((row) => ({
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

test('50 compact rows a page; page and words live in the address', async ({ page }, testInfo) => {
  const state = await historyFixture(page, testInfo)
  await page.goto('/history')
  await expect(page.getByRole('heading', { level: 1, name: '대화 기록' })).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('[data-history-total]')).toContainText('대화 120개가 있습니다')
  await expect(historyRows(page)).toHaveCount(50)
  await expect(page.locator('[data-history-range]')).toHaveText('120개 중 1–50')
  await expect(pager(page).getByRole('button', { name: '1쪽' })).toHaveAttribute('aria-current', 'page')
  // One line per conversation (the phone's larger type and touch target make it a little taller).
  const box = await historyRows(page).first().boundingBox()
  expect(box!.height).toBeLessThanOrEqual(testInfo.project.name === 'phone' ? 64 : 48)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/history-paged-${testInfo.project.name}.png` })

  await pager(page).getByRole('button', { name: '2쪽' }).click()
  await expect(page).toHaveURL(/\/history\?page=2$/)
  await expect(page.locator('[data-history-range]')).toHaveText('120개 중 51–100')
  await expect(historyRows(page).first()).toContainText(rows[50].title)

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
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/history-search-paged-${testInfo.project.name}.png` })
  expect(state.problems).toEqual([])
})
