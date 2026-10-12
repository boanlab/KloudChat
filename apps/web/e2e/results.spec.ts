import { expect, test, type Page } from '@playwright/test'
import {
  card, deckSession, deckTitle, openSession, panel, reportSession, reportTitle, resultsFixture, shareToken,
} from './results-fixture'
import { viewport } from './project'

/** The result card under the answer that made a document: open, download, start again from it. */

test.use({ serviceWorkers: 'block' })

const SHOTS = process.env.PHASE2_SHOTS

async function shot(page: Page, name: string, project: string) {
  if (!SHOTS) return
  // Menus fade in; the picture is of the settled screen.
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/${name}-${project}.png` })
}

/** The panel opens itself with the session; the card sits behind it on a phone. */
/** Both fixture conversations open their document in the panel once the conversation has
 *  loaded, which can be after the transcript shows: wait for it to open, then close it. A
 *  one-time look raced that opening and left the panel to cover the card. */
async function closePanel(page: Page) {
  await expect(panel(page)).toBeVisible({ timeout: 10_000 })
  await panel(page).getByRole('button', { name: '닫기', exact: true }).last().click()
  await expect(panel(page)).toBeHidden()
}

const menu = (page: Page) => page.locator('[role="menu"]:visible').first()

/** The address moves before the new screen renders (a transition); until then the old box is still on screen. */
async function onNewScreen(page: Page) {
  await expect(page.getByRole('heading', { level: 1, name: /안녕하세요/ })).toBeVisible()
  await expect(page.getByTestId('transcript')).toHaveCount(0)
}

test('the answer that wrote the report carries its card; the question card does not', async ({ page }, testInfo) => {
  const recorded = await resultsFixture(page, testInfo)
  await openSession(page, reportSession)
  await closePanel(page)
  await expect(card(page)).toHaveCount(1)
  const answer = page.getByText('5개 섹션으로 보고서를 작성했습니다.').locator('xpath=ancestor::div[contains(@class,"min-w-0 flex-1")][1]')
  await expect(answer.locator('[data-result-card]')).toBeVisible()
  await expect(card(page)).toContainText(reportTitle)
  await expect(card(page)).toContainText('보고서 · 5개 절 · v1')
  await card(page).scrollIntoViewIfNeeded()
  await shot(page, 'result-card', viewport(testInfo))

  await card(page).getByRole('button', { name: '열기' }).click()
  await expect(panel(page)).toBeVisible()
  await expect(panel(page).getByText(reportTitle).first()).toBeVisible()
  expect(recorded.unexpected).toEqual([])
  expect(recorded.problems).toEqual([])
})

test('the card downloads every report format by the export endpoint, after the check', async ({ page }, testInfo) => {
  const recorded = await resultsFixture(page, testInfo)
  await openSession(page, reportSession)
  await closePanel(page)
  await card(page).getByRole('button', { name: '내려받기' }).click()
  // The uncited 30% is said before the formats, as 파일 › 내보내기 did.
  await expect(menu(page)).toContainText('내려받기 전 확인')
  await expect(menu(page).getByRole('menuitem', { name: /내보내기 전 근거 확인 \d+건/ })).toBeVisible()
  for (const name of ['PDF', 'Word 문서', '한글 문서', '마크다운 원문']) {
    await expect(menu(page).getByRole('menuitem', { name: new RegExp(name) })).toBeVisible()
  }
  await shot(page, 'download-menu', viewport(testInfo))
  const download = page.waitForEvent('download')
  await menu(page).getByRole('menuitem', { name: /Word 문서/ }).click()
  expect((await download).suggestedFilename()).toMatch(/\.docx$/)
  expect(recorded.exports).toEqual(['report:docx'])

  // 「먼저 확인」 opens the panel on the review pane.
  await card(page).getByRole('button', { name: '내려받기' }).click()
  await menu(page).getByRole('menuitem', { name: /내보내기 전 근거 확인/ }).click()
  await expect(panel(page).getByRole('heading', { name: '참고문헌' })).toBeVisible()
  await expect(panel(page).getByRole('region', { name: '검토' })).toBeVisible()
})

test('a deck card offers the deck formats', async ({ page }, testInfo) => {
  const recorded = await resultsFixture(page, testInfo)
  await openSession(page, deckSession)
  await closePanel(page)
  await expect(card(page)).toContainText(deckTitle)
  await expect(card(page)).toContainText('슬라이드 · 4장 · v1')
  await card(page).getByRole('button', { name: '내려받기' }).click()
  for (const name of ['PowerPoint', 'PDF \\(발표용\\)', '텍스트 \\(노트 포함\\)']) {
    await expect(menu(page).getByRole('menuitem', { name: new RegExp(name) })).toBeVisible()
  }
  const download = page.waitForEvent('download')
  await menu(page).getByRole('menuitem', { name: /PowerPoint/ }).click()
  expect((await download).suggestedFilename()).toMatch(/\.pptx$/)
  expect(recorded.exports).toEqual(['deck:pptx'])
})

test('「이걸로 새로 시작」 attaches the document to a new report with the request written', async ({ page }, testInfo) => {
  const recorded = await resultsFixture(page, testInfo)
  await openSession(page, reportSession)
  await closePanel(page)
  await card(page).scrollIntoViewIfNeeded()
  await card(page).getByRole('button', { name: '더보기' }).click()
  await shot(page, 'reuse-menu', viewport(testInfo))
  await menu(page).getByRole('menuitem', { name: '이걸로 새로 시작' }).click()
  await expect(page).toHaveURL(/\/new\/report$/)
  await onNewScreen(page)
  expect(recorded.exports).toEqual(['report:md'])
  expect(recorded.uploads).toEqual([`${reportTitle}.md`])
  await expect(page.getByText(`${reportTitle}.md`).first()).toBeVisible()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(`「${reportTitle}」 내용을 바탕으로 새 보고서를 써 주세요.`)
  // The conversation left behind did not keep the request as its own draft.
  await page.goBack()
  await expect(page.getByTestId('transcript')).toBeVisible()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
})

test('「새 대화에 첨부」 attaches the deck and leaves the box to the person', async ({ page }, testInfo) => {
  const recorded = await resultsFixture(page, testInfo)
  await openSession(page, deckSession)
  await closePanel(page)
  await card(page).getByRole('button', { name: '더보기' }).click()
  await menu(page).getByRole('menuitem', { name: '새 대화에 첨부' }).click()
  await expect(page).toHaveURL(/\/new\/slides$/)
  await onNewScreen(page)
  expect(recorded.exports).toEqual(['deck:md'])
  expect(recorded.uploads).toEqual([`${deckTitle}.md`])
  await expect(page.getByText(`${deckTitle}.md`).first()).toBeVisible()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
})

test('an answer whose version was later edited opens that version', async ({ page }, testInfo) => {
  await resultsFixture(page, testInfo, { reportVersion: 2 })
  await openSession(page, reportSession)
  await closePanel(page)
  await expect(card(page)).toContainText('v1 · 지금 v2')
  await card(page).getByRole('button', { name: '열기' }).click()
  const dialog = page.getByRole('dialog', { name: '버전 기록' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('region', { name: 'v1 내용 미리보기' })).toBeVisible()
})

test('the 아티팩트 page offers the same reuse', async ({ page }, testInfo) => {
  const recorded = await resultsFixture(page, testInfo)
  await page.goto('/artifacts')
  await page.getByRole('button', { name: `${reportTitle} 더보기` }).click()
  await expect(menu(page).getByRole('menuitem', { name: '이걸로 새로 시작' })).toBeVisible()
  await menu(page).getByRole('menuitem', { name: '새 대화에 첨부' }).click()
  await expect(page).toHaveURL(/\/new\/report$/)
  await onNewScreen(page)
  expect(recorded.uploads).toEqual([`${reportTitle}.md`])
  await expect(page.getByText(`${reportTitle}.md`).first()).toBeVisible()
})

test('a shared conversation shows the card with nothing to change it', async ({ page }, testInfo) => {
  await resultsFixture(page, testInfo)
  await page.goto(`/share/${shareToken}`)
  await expect(card(page)).toHaveCount(1)
  await expect(card(page)).toContainText('보고서 · 5개 절')
  await expect(card(page).getByRole('button', { name: '내려받기' })).toHaveCount(0)
  await expect(card(page).getByRole('button', { name: '더보기' })).toHaveCount(0)
  await card(page).getByRole('button', { name: '보기' }).click()
  await shot(page, 'shared-card', viewport(testInfo))
})

test('a deck saved in the panel moves the card under its answer to the new version', async ({ page }, testInfo) => {
  const recorded = await resultsFixture(page, testInfo)
  await openSession(page, deckSession)
  await expect(panel(page)).toBeVisible()
  const header = panel(page).locator('header').first()
  await header.getByRole('button', { name: '편집', exact: true }).click()
  await panel(page).getByLabel('슬라이드 텍스트').fill('행사 신청 흐름\n고친 첫 장')
  await header.getByRole('button', { name: '저장' }).click()
  await expect(panel(page).getByLabel('슬라이드 텍스트')).toHaveCount(0)
  await closePanel(page)
  await expect(card(page)).toContainText('v1 · 지금 v2')
  expect(recorded.unexpected).toEqual([])
})
