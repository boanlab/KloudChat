import { expect, test, type Page } from '@playwright/test'
import { signInAs } from './helpers'

/**
 * 서식의 CSS 는 패널 밖으로 나가지 않는다.
 *
 * Paged.js publishes every stylesheet it is given to `document.head`, and a
 * 서식 is a stylesheet for a whole document: it sets `body`, bare `h1`, bare
 * `p`, `table`. Opening a 명조 report turned the sidebar, the composer and
 * every menu in the product serif — and left them that way after the panel
 * closed, because the styles stay in `<head>`.
 *
 * The font is what somebody notices. It is not the only thing that crossed:
 * paragraph margins, list padding, table width and the page's white background
 * came with it, so the check is on the app's own computed style rather than on
 * one property.
 */

const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }
/** A stored report, which opens in the page view — the surface that runs Paged.js. */
const REPORT = '/s/89c60307be8a4ad6a9873ebc701f9733'

/** What the application shell is wearing, as the browser resolves it. */
async function shell(page: Page) {
  return await page.evaluate(() => {
    const body = getComputedStyle(document.body)
    const probe = document.createElement('p')
    probe.textContent = '측정'
    document.body.appendChild(probe)
    const paragraph = getComputedStyle(probe)
    const measured = {
      font: body.fontFamily,
      background: body.backgroundColor,
      paragraphMargin: paragraph.marginBottom,
      paragraphFont: paragraph.fontFamily,
    }
    probe.remove()
    return measured
  })
}

test('보고서를 페이지뷰로 열어도 앱의 글꼴과 여백은 그대로다', async ({ page }) => {
  test.setTimeout(300_000)
  await signInAs(page, USER.email, USER.password)

  // Measured on a screen with no document on it.
  await page.goto('/history')
  await expect(page.getByLabel('대화 검색')).toBeVisible({ timeout: 20_000 })
  const before = await shell(page)

  await page.goto(REPORT)
  await expect(page.locator('[data-panel="artifact"]')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByLabel('중지')).toBeHidden({ timeout: 180_000 })
  // The pages are laid out; the stylesheet is in the document by now.
  await expect(page.locator('.paged-report-preview .pagedjs_page').first()).toBeVisible({
    timeout: 60_000,
  })

  const during = await shell(page)
  expect(during, '문서를 여는 동안 앱의 글꼴·배경·여백이 바뀌었습니다').toEqual(before)

  // And the document still got its own 서식 — scoping must not have emptied it.
  const documentFont = await page
    .locator('.paged-report-preview .pagedjs_page_content')
    .first()
    .evaluate((el) => getComputedStyle(el).fontFamily)
  expect(documentFont, '문서가 서식을 잃었습니다').toBeTruthy()

  // Closed, and nothing left behind in <head>.
  await page.goto('/history')
  await expect(page.getByLabel('대화 검색')).toBeVisible({ timeout: 20_000 })
  const after = await shell(page)
  expect(after, '패널을 닫은 뒤에도 서식이 앱에 남아 있습니다').toEqual(before)
  const leftovers = await page.evaluate(
    () => document.head.querySelectorAll('style[data-pagedjs-inserted-styles]').length,
  )
  expect(leftovers, 'head 에 남은 Paged.js 스타일시트').toBe(0)
})
