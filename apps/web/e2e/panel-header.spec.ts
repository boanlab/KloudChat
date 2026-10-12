import { expect, test, type Page } from '@playwright/test'
import { deckSession, panel, reportSession, resultsFixture } from './results-fixture'
import { viewport } from './project'

/** The report and slide panels read calmly: title, view, 검토, 내려받기, 편집, ⋯ in one row;
 *  the ribbon shows only while editing and holds only editing tabs. */

test.use({ serviceWorkers: 'block' })

const SHOTS = process.env.PHASE2_SHOTS

async function shot(page: Page, name: string, project: string) {
  if (!SHOTS) return
  // Menus fade in; the picture is of the settled screen.
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/${name}-${project}.png` })
}

const header = (page: Page) => panel(page).locator('header').first()
const menu = (page: Page) => page.locator('[role="menu"]:visible').first()
const tabs = async (page: Page) => panel(page).getByRole('tab').allTextContents()

async function open(page: Page, session: string) {
  await page.goto(`/s/${session}`)
  await expect(panel(page)).toBeVisible()
}

/** Every visible header control sits inside the screen, in one row. */
async function fitsOneRow(page: Page) {
  const boxes = await header(page).locator('> div').first().locator('button:visible').evaluateAll((buttons) =>
    buttons.map((b) => b.getBoundingClientRect()).map((r) => ({ middle: r.top + r.height / 2, right: Math.round(r.right) })))
  const width = page.viewportSize()!.width
  expect(boxes.every((b) => b.right <= width + 1)).toBe(true)
  const middles = boxes.map((b) => b.middle)
  expect(Math.max(...middles) - Math.min(...middles)).toBeLessThan(4)
}

test('report: reading shows no ribbon and downloads from the header', async ({ page }, testInfo) => {
  const recorded = await resultsFixture(page, testInfo)
  await open(page, reportSession)
  await expect(panel(page).getByRole('tablist')).toHaveCount(0)
  await fitsOneRow(page)
  await shot(page, 'report-reading', viewport(testInfo))

  const download = header(page).getByRole('button', { name: '내려받기' })
  await expect(download).toBeInViewport()
  await download.click()
  await expect(menu(page).getByRole('menuitem', { name: /내보내기 전 근거 확인|먼저 근거 확인/ }).first()).toBeVisible()
  for (const name of ['PDF', 'Word 문서', '한글 문서', '마크다운 원문', '인쇄']) {
    await expect(menu(page).getByRole('menuitem', { name: new RegExp(name) })).toBeVisible()
  }
  await shot(page, 'report-download', viewport(testInfo))
  const file = page.waitForEvent('download')
  await menu(page).getByRole('menuitem', { name: /한글 문서/ }).click()
  expect((await file).suggestedFilename()).toMatch(/\.hwpx$/)
  expect(recorded.exports).toEqual(['report:hwpx'])

  // 검토 opens the review pane while reading: checks, stages, versions and the sources.
  await header(page).getByRole('button', { name: /^검토/ }).click()
  await expect(panel(page).getByRole('heading', { name: '참고문헌' })).toBeVisible()
  await expect(panel(page).getByRole('region', { name: '검토' }).getByRole('button', { name: /버전 기록/ })).toBeVisible()
})

test('report: ⋯ holds the views it folded, the contents, versions and reuse', async ({ page }, testInfo) => {
  await resultsFixture(page, testInfo)
  await open(page, reportSession)
  await header(page).getByRole('button', { name: '더보기' }).click()
  for (const name of ['목차', '원문 편집', '버전 기록', '이걸로 새로 시작', '새 대화에 첨부']) {
    await expect(menu(page).getByRole('menuitem', { name: new RegExp(name) })).toBeVisible()
  }
  await shot(page, 'report-more', viewport(testInfo))
  await menu(page).getByRole('menuitem', { name: /버전 기록/ }).click()
  await expect(page.getByRole('dialog', { name: '버전 기록' })).toBeVisible()
})

test('report: 편집 opens the editing tabs; 테마 holds the look, 강조색, 템플릿 and the page', async ({ page }, testInfo) => {
  await resultsFixture(page, testInfo)
  await open(page, reportSession)
  await header(page).getByRole('button', { name: '편집', exact: true }).click()
  expect(await tabs(page)).toEqual(['글자', '삽입', '테마', '검토'])
  await expect(panel(page).getByRole('tab', { name: '글자' })).toHaveAttribute('aria-selected', 'true')
  await shot(page, 'report-editing', viewport(testInfo))
  await panel(page).getByRole('tab', { name: '테마' }).click()
  await expect(header(page).getByRole('button', { name: '문서 테마 고르기' })).toBeVisible()
  await expect(header(page).getByRole('button', { name: '강조색 고르기' })).toBeVisible()
  await expect(header(page).getByRole('button', { name: '페이지 설정' })).toBeVisible()
  // 템플릿 is the document's structure: the menu lists the templates.
  await header(page).getByRole('button', { name: '문서 템플릿 고르기' }).click()
  await expect(menu(page)).toContainText('내보내면 이 템플릿으로 나갑니다')
  await shot(page, 'report-design-tab', viewport(testInfo))
  await page.keyboard.press('Escape')
  await panel(page).getByRole('tab', { name: '검토' }).click()
  await expect(header(page).getByRole('button', { name: /출처 \d+/ })).toBeVisible()
  // 편집 again goes back to reading.
  await header(page).getByRole('button', { name: '편집', exact: true }).click()
  await expect(panel(page).getByRole('tablist')).toHaveCount(0)
})

test('slides: 발표 and 내려받기 in the header, editing tabs only while editing', async ({ page }, testInfo) => {
  const recorded = await resultsFixture(page, testInfo)
  await open(page, deckSession)
  await expect(panel(page).getByRole('tablist')).toHaveCount(0)
  await fitsOneRow(page)
  await expect(header(page).getByRole('button', { name: '발표' })).toBeVisible()
  await shot(page, 'deck-reading', viewport(testInfo))
  await header(page).getByRole('button', { name: '내려받기' }).click()
  for (const name of ['PowerPoint', 'PDF \\(발표용\\)', '텍스트']) {
    await expect(menu(page).getByRole('menuitem', { name: new RegExp(name) })).toBeVisible()
  }
  const file = page.waitForEvent('download')
  await menu(page).getByRole('menuitem', { name: /PDF/ }).click()
  expect((await file).suggestedFilename()).toMatch(/\.pdf$/)
  expect(recorded.exports).toEqual(['deck:pdf'])

  await header(page).getByRole('button', { name: /^검토/ }).click()
  await expect(panel(page).getByRole('complementary', { name: '검토 메모' })).toBeVisible()
  await expect(panel(page).getByLabel('검토 도구').getByRole('button', { name: /버전 기록/ })).toBeVisible()
  await panel(page).getByRole('button', { name: '검토 메모 닫기' }).click()

  await header(page).getByRole('button', { name: '더보기' }).click()
  for (const name of ['이 장 다시 만들기', '버전 기록', '이걸로 새로 시작', '새 대화에 첨부']) {
    await expect(menu(page).getByRole('menuitem', { name: new RegExp(name) })).toBeVisible()
  }
  await page.keyboard.press('Escape')

  await header(page).getByRole('button', { name: '편집', exact: true }).click()
  expect(await tabs(page)).toEqual(['글자', '삽입', '테마', '검토'])
  await expect(header(page).getByRole('button', { name: '저장' })).toBeVisible()
  await shot(page, 'deck-editing', viewport(testInfo))
  await header(page).getByRole('button', { name: '편집 취소' }).click()
  await panel(page).getByRole('tab', { name: '테마' }).click()
  await expect(header(page).getByRole('button', { name: '슬라이드 테마 고르기' })).toBeVisible()
  await expect(header(page).getByRole('button', { name: '강조색 고르기' })).toBeVisible()
  await shot(page, 'deck-design-tab', viewport(testInfo))
})

test('slides: a table slide opens its cells to edit, under labels that say which box is which', async ({ page }, testInfo) => {
  await resultsFixture(page, testInfo)
  await open(page, deckSession)
  // The third slide is the table.
  await panel(page).getByRole('button', { name: '다음 장' }).click()
  await panel(page).getByRole('button', { name: '다음 장' }).click()
  await header(page).getByRole('button', { name: '편집', exact: true }).click()
  await expect(panel(page).getByLabel('슬라이드 텍스트')).toBeVisible()
  await expect(panel(page).getByText('이 장 편집', { exact: true })).toBeVisible()
  // No click on the drawn table is needed to reach its cells.
  await expect(panel(page).getByLabel('1행 1열')).toBeVisible()
  await panel(page).getByLabel('1행 1열').fill('기준(수정)')
  await expect(panel(page).getByLabel('슬라이드 텍스트')).toHaveValue(/기준\(수정\)/)
  await shot(page, 'deck-table-edit', viewport(testInfo))
})
