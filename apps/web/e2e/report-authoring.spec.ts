/**
 * A person with a report due, from the empty composer to a file they can send.
 *
 * The other report suites each check one affordance works. This one refuses to
 * do that: it walks the whole job in order, the way somebody with a deadline
 * walks it, and every step it cannot complete is a step that person cannot
 * complete either. The verdict it produces is not "the button exists" but "the
 * document got written".
 *
 * The job, in the order it actually happens:
 *
 *   1. ask for a report on a subject with checkable figures in it
 *   2. look at what was proposed and approve it
 *   3. read the result as pages, in the 서식 it will be submitted in
 *   4. fix a sentence by hand — this is where most of the time goes
 *   5. make the fix survive: save it, come back, still there
 *   6. check a figure against the web
 *   7. take the file away
 *
 * Nothing here asserts on a toast. A save is proven by reloading, an export by
 * opening the bytes.
 */

import { expect, test, type Page } from '@playwright/test'
import { approvePlan, signIn } from './helpers'

//: The model writes this, and a small model has moods — see report-personas.
test.describe.configure({ retries: 1, mode: 'serial' })
test.setTimeout(600_000)

const REQUEST =
  '사내 회의실 예약 시스템 교체 검토 보고서를 써 줘. ' +
  '현행 시스템의 문제와 대안 두 가지를 비교하고, 마지막에 다음 행동을 적어 줘.'

/** The page view's document, inside the shadow root the editor draws in. */
function sheet(page: Page) {
  // `.page` is the template's own root. Playwright pierces shadow roots for
  // CSS selectors, which is the only reason this reads like an ordinary one.
  return page.locator('.page')
}

async function openReport(page: Page) {
  await page.goto('/new/report')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await page.getByLabel('프롬프트 입력').fill(REQUEST)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await approvePlan(page)
  // The artifact panel opens itself when the document lands.
  await expect(page.getByRole('button', { name: '내보내기' })).toBeVisible({
    timeout: 480_000,
  })
}

test.describe('보고서를 처음부터 끝까지 쓴다', () => {
  test('1. 요청한 보고서가 절을 갖추고 나온다', async ({ page }) => {
    await signIn(page)
    await openReport(page)

    // A report is sections, not one wall of text. Under three, the outline
    // step did not really happen.
    const headings = page.locator('article h2')
    await expect.poll(() => headings.count(), { timeout: 60_000 }).toBeGreaterThanOrEqual(3)
  })

  test('2. 페이지뷰가 서식을 입힌 종이로 보여 준다', async ({ page }) => {
    await signIn(page)
    await openReport(page)

    await page.getByRole('button', { name: '페이지뷰' }).click()

    // The template's own root, which only exists if its stylesheet loaded and
    // the shadow root took it.
    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })
    // And the seed is actually styling it — a bare div has no serif stack and
    // no paper width.
    const width = await sheet(page).evaluate((el) => el.getBoundingClientRect().width)
    expect(width).toBeGreaterThan(600)
  })

  test('3. 문단을 눌러 그 자리에서 고칠 수 있다', async ({ page }) => {
    await signIn(page)
    await openReport(page)
    await page.getByRole('button', { name: '페이지뷰' }).click()
    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })

    const paragraph = page.locator('.ProseMirror p').first()
    await paragraph.click()
    await paragraph.pressSequentially('직접 고친 문장. ')

    await expect(paragraph).toContainText('직접 고친 문장.')
    // The bar has to wake up, or the person can type but not format.
    await expect(page.getByRole('button', { name: '굵게' })).toBeEnabled()
  })

  test('4. 손으로 고친 것이 저장되고 다시 열어도 남는다', async ({ page }) => {
    await signIn(page)
    await openReport(page)
    await page.getByRole('button', { name: '페이지뷰' }).click()
    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })

    const mark = `수기수정-${Date.now()}`
    const paragraph = page.locator('.ProseMirror p').first()
    await paragraph.click()
    await paragraph.pressSequentially(`${mark} `)

    await page.getByRole('button', { name: '저장', exact: true }).click()
    // Proven by the round trip, not by the button going quiet.
    await page.reload()
    await expect(page.getByText(mark).first()).toBeVisible({ timeout: 30_000 })
  })

  test('5. 서식을 바꿔 끼울 수 있다', async ({ page }) => {
    await signIn(page)
    await openReport(page)
    await page.getByRole('button', { name: '페이지뷰' }).click()
    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })

    // Whichever 서식 is showing, another one must be reachable — a choice made
    // once at generation is the thing the page view exists to undo.
    // In the panel, not on the page: the composer's own 서식 고르기 matches
    // this name too, and the one being tested is the panel's switcher — which
    // is labelled with whichever 서식 the document is currently wearing.
    await page
      .getByRole('complementary')
      .last()
      .getByRole('button', { name: /보고 문서|서식/ })
      .click()
    // `menuitemcheckbox`, not `menuitem`: the switcher marks whichever 서식 the
    // document is wearing, and a checked item is a different ARIA role.
    await page.getByRole('menuitemcheckbox', { name: '한 장 요약' }).click()
    await expect(sheet(page)).toBeVisible({ timeout: 30_000 })
  })

  test('6. 수치를 웹에 대고 검토할 수 있다', async ({ page }) => {
    await signIn(page)
    await openReport(page)

    // The web view is where the verdicts are read.
    //
    // Reached through the section's own menu. 검토 and 다시 쓰기 used to sit
    // beside the heading and appear only on hover, next to a 절 편집 button
    // that was always there — one row, two rules, and the hidden half read as
    // absent. Everything a section can do is in the one handle now.
    await page.getByRole('button', { name: /절 편집$/ }).first().click()
    await page.getByRole('menuitem', { name: /^검토$|^다시 검토$/ }).click()
    // Either answer means the check ran. A section with figures in it comes
    // back as verdicts under a 팩트체크 heading; one without comes back saying
    // so, because opinions and definitions are not judged. Which of the two
    // the first section draws is up to what the model wrote, and insisting on
    // the first made this a test of the prose rather than of the control.
    await expect(
      page
        .getByText('팩트체크')
        .or(page.getByText('검색으로 확인할 수 있는 주장이 여기에는 없습니다')),
    ).toBeVisible({ timeout: 180_000 })
  })

  test('7. 제출할 파일이 나온다', async ({ page }) => {
    await signIn(page)
    await openReport(page)

    const download = page.waitForEvent('download', { timeout: 120_000 })
    await page.getByRole('button', { name: '내보내기' }).click()
    await page.getByRole('menuitem', { name: /docx/i }).click()
    const file = await download

    const path = await file.path()
    expect(path).toBeTruthy()
    // A .docx is a zip; anything else is an error page with a filename.
    const { readFileSync } = await import('node:fs')
    expect(readFileSync(path!).subarray(0, 2).toString()).toBe('PK')
  })
})
