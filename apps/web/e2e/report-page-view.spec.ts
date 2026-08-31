/**
 * What the page view promises, on a report that already exists.
 *
 * Fast on purpose — it opens a stored artifact rather than paying for a
 * generation — so the invariants below are checked on every run instead of
 * only when somebody suspects them.
 *
 * The promises, after the sheet-splitting was taken out:
 *
 *   * the paper is A4 wide, whatever the panel is
 *   * the document is one continuous sheet, and the page breaks are guides
 *   * nothing writes layout onto the nodes ProseMirror owns
 *   * opening a document does not make it look edited
 *
 * The one that is *not* promised is where a page ends. That is the print
 * engine's answer and this file deliberately does not assert it.
 */

import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

async function openPageView(page: import('@playwright/test').Page) {
  await signIn(page)
  await page.goto('/artifacts')
  await page.getByRole('button', { name: /보고/ }).first().click()
  await expect(page.getByRole('button', { name: '내보내기' })).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: '페이지뷰' }).click()
  await expect(page.locator('.page')).toBeVisible({ timeout: 30_000 })
}

test('종이는 패널이 좁아도 A4 폭이다', async ({ page }) => {
  await openPageView(page)

  // Measured, not asserted from the style: `max-width: 100%` used to squeeze
  // an "A4" into a 352px panel, where the line length and the margins were all
  // wrong and the document read as prose in a column.
  const width = await page.locator('.page').evaluate((el) => el.getBoundingClientRect().width)
  expect(width).toBeGreaterThan(700)
})

test('쪽 나눔을 흉내 내려고 문서를 건드리지 않는다', async ({ page }) => {
  await openPageView(page)

  // The sheet-splitting wrote margins onto ProseMirror's own nodes to carry a
  // block onto the next page. It never converged, and it fought the editor for
  // the same attribute. Nothing may write layout here again.
  const written = await page.locator('.page').evaluate((el) => {
    const nodes = Array.from(el.querySelectorAll<HTMLElement>('.ProseMirror > *'))
    return nodes.filter((n) => n.style.marginTop !== '').length
  })
  expect(written).toBe(0)
})

test('문서를 열기만 해서는 고쳐진 것이 되지 않는다', async ({ page }) => {
  await openPageView(page)

  // Tiptap rewrites the HTML into its own shape on load. Reporting that as an
  // edit put a 저장 button on every report the moment it opened, which teaches
  // people the button means nothing.
  await expect(page.getByRole('button', { name: '저장', exact: true })).toBeHidden()
})

test('여러 쪽짜리 문서는 쪽 경계를 참고선으로 보여 준다', async ({ page }) => {
  await openPageView(page)

  const height = await page.locator('.page').evaluate((el) => el.scrollHeight)
  test.skip(height < 1200, '한 쪽짜리 문서라 경계가 없다')

  // "around page 2", not "page 2". The wording is the promise: the screen
  // says roughly where, and the printed file says exactly.
  await expect(page.getByText(/쪽 즈음/).first()).toBeVisible()
})


test('서식의 색이 실제로 적용된다', async ({ page }) => {
  await openPageView(page)

  /*
   * The seed declares its palette on `:root`, and in a shadow tree `:root`
   * matches nothing — it means the document element, which is outside the
   * boundary. Custom properties inherit through that boundary, so what filled
   * the gap was the app's own tokens: hairlines came out the app's pale blue,
   * and a header rule drawn with `--rule` disappeared entirely because nothing
   * outside defines it.
   *
   * The template's own values, read from inside, are the proof that the
   * document is being drawn in the template rather than in the app.
   */
  const tokens = await page.locator('.page').evaluate((el) => {
    const s = getComputedStyle(el)
    return {
      rule: s.getPropertyValue('--rule').trim(),
      border: s.getPropertyValue('--border').trim(),
      paper: s.getPropertyValue('--paper').trim(),
    }
  })
  expect(tokens.rule).not.toBe('')
  expect(tokens.border).toContain('color-mix')
  expect(tokens.paper).not.toBe('')
})
