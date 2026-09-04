/** Writing a report end to end: request, approve, page view, hand edit, save, fact-check, export. */

import { expect, test, type Page } from '@playwright/test'
import { approvePlan, artifactReady, ribbonTab, signIn } from './helpers'

// Retried: the content is model-written.
test.describe.configure({ retries: 1, mode: 'serial' })
test.setTimeout(600_000)

const REQUEST =
  '사내 회의실 예약 시스템 교체 검토 보고서를 써 줘. ' +
  '현행 시스템의 문제와 대안 두 가지를 비교하고, 마지막에 다음 행동을 적어 줘.'

/** The template's root (`.page`) inside the editor's shadow root; CSS locators pierce it. */
function sheet(page: Page) {
  return page.locator('.page').first()
}

async function openReport(page: Page) {
  await page.goto('/new/report')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await page.getByLabel('프롬프트 입력').fill(REQUEST)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await approvePlan(page)
  await artifactReady(page)
}

test.describe('보고서를 처음부터 끝까지 쓴다', () => {
  test('1. 요청한 보고서가 절을 갖추고 나온다', async ({ page }) => {
    await signIn(page)
    await openReport(page)

    // Under three sections, the outline step did not happen.
    const headings = page.locator('article h2')
    await expect.poll(() => headings.count(), { timeout: 60_000 }).toBeGreaterThanOrEqual(3)
  })

  test('2. 페이지뷰가 서식을 입힌 종이로 보여 준다', async ({ page }) => {
    await signIn(page)
    await openReport(page)

    await page.getByRole('button', { name: '페이지뷰' }).click()

    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })
    // A bare div has no paper width.
    const width = await sheet(page).evaluate((el) => el.getBoundingClientRect().width)
    expect(width).toBeGreaterThan(600)
  })

  test('3. 문단을 눌러 그 자리에서 고칠 수 있다', async ({ page }) => {
    await signIn(page)
    await openReport(page)
    // 문서 수정 edits in place; 페이지뷰 is read-only.
    await page.getByRole('button', { name: '문서 수정' }).click()
    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })

    const paragraph = page.locator('.ProseMirror p').first()
    await paragraph.click()
    await paragraph.pressSequentially('직접 고친 문장. ')

    await expect(paragraph).toContainText('직접 고친 문장.')
    await expect(page.getByRole('button', { name: '굵게' })).toBeEnabled()
  })

  test('4. 손으로 고친 것이 저장되고 다시 열어도 남는다', async ({ page }) => {
    await signIn(page)
    await openReport(page)
    await page.getByRole('button', { name: '문서 수정' }).click()
    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })

    const mark = `수기수정-${Date.now()}`
    const paragraph = page.locator('.ProseMirror p').first()
    await paragraph.click()
    await paragraph.pressSequentially(`${mark} `)

    // Wait for the PATCH: a reload mid-save cancels the write.
    const saved = page.waitForResponse((r) => /\/api\/artifacts\/[^/]+$/.test(r.url()) && r.request().method() === 'PATCH')
    await page.getByRole('button', { name: '저장', exact: true }).click()
    await saved
    await page.reload()
    await expect(page.getByText(mark).first()).toBeVisible({ timeout: 30_000 })
  })

  test('5. 서식을 바꿔 끼울 수 있다', async ({ page }) => {
    await signIn(page)
    await openReport(page)
    await page.getByRole('button', { name: '페이지뷰' }).click()
    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })

    // The panel's switcher, labelled with the current 서식; the composer's picker matches the name too.
    await page
      .getByRole('complementary')
      .last()
      .getByRole('button', { name: /보고 문서|서식/ })
      .click()
    // `menuitemcheckbox`: the switcher marks the current 서식.
    await page.getByRole('menuitemcheckbox', { name: '한 장 요약' }).click()
    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })
  })

  test('6. 수치를 웹에 대고 검토할 수 있다', async ({ page }) => {
    await signIn(page)
    await openReport(page)

    // Verdicts are read in the web view, via the section's menu.
    await page.getByRole('button', { name: /절 편집$/ }).first().click()
    await page.getByRole('menuitem', { name: /^검토$|^다시 검토$/ }).click()
    // Either answer means the check ran; a section without claims says so.
    await expect(
      page
        .getByText('팩트체크')
        .or(page.getByText('검색으로 확인할 수 있는 주장이 여기에는 없습니다')),
    ).toBeVisible({ timeout: 180_000 })
  })

  test('7. 제출할 파일이 나온다', async ({ page }) => {
    await signIn(page)
    await openReport(page)

    await ribbonTab(page, '파일')
    const download = page.waitForEvent('download', { timeout: 120_000 })
    await page.getByRole('button', { name: '내보내기' }).click()
    await page.getByRole('menuitem', { name: /docx/i }).click()
    const file = await download

    const path = await file.path()
    expect(path).toBeTruthy()
    // A .docx is a zip.
    const { readFileSync } = await import('node:fs')
    expect(readFileSync(path!).subarray(0, 2).toString()).toBe('PK')
  })
})
