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
import { E2E_ADMIN, openAndSeedReport, signIn } from './helpers'

async function openPageView(page: import('@playwright/test').Page) {
  await signIn(page)
  await page.goto('/artifacts')
  // The kind first, then the newest of that kind.
  //
  // `/보고/` over the whole page also matches the sidebar, where a conversation
  // called "…보고서를 써 줘" sits above every card — clicking that navigates to
  // the session, and every assertion below then runs against a transcript.
  // Scoping to the grid fixes that and introduces the other half of the same
  // mistake: the newest card in the gallery is whatever was made last, which is
  // as often a deck. The tab is what says which kind this file is about.
  await page.getByRole('tab', { name: /보고서/ }).click()
  await page
    .locator('div.grid')
    .getByRole('button', { name: /열기$/ })
    .first()
    .click()
  await expect(page.getByRole('button', { name: '내보내기' })).toBeVisible({ timeout: 30_000 })

  // Pressed only when it is not already showing.
  //
  // The toggle's `aria-label` is 페이지뷰 in both directions — the label names
  // the destination, not the state — and a document written into a 서식 opens
  // *in* the page view, because the panel reads `templateId` and starts there.
  // So clicking it unconditionally took such a document to the web view, and
  // then every promise below was checked against a screen with no paper on it.
  // Which document that is depends on what was written last.
  // Read off the button's own words, not off the paper. `.page` is absent for
  // a beat while the panel mounts, so counting it says "web view" about a
  // document that is on its way to showing pages — and the click then takes it
  // the wrong way.
  const toggle = page.getByRole('button', { name: '페이지뷰' })
  if ((await toggle.innerText()) !== '웹뷰') {
    await toggle.click()
  }
  await expect(page.locator('.page').first()).toBeVisible({ timeout: 30_000 })
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

test('서식을 바꾸면 문서가 그 양식으로 나간다', async ({ page }) => {
  // 이 자리에 있던 컨트롤은 아무것도 하지 않고 있었다.
  //
  // 서식마다 조판이 따로였을 때는 이것이 보기 방식이었다 — 어느 종이에 그릴지
  // 고르는 것. 조판이 하나로 합쳐지면서 열 개 항목이 한 화면을 냈고, 고르든
  // 말든 달라지는 것이 없었다. 문서에도 파일에도 쓰지 않는 지역 상태였다.
  //
  // 지금 서식이 정하는 것은 내보낸 파일이다. 그래서 이 선택은 문서에 남고,
  // 남아야 내보내기가 그 양식으로 연다.
  const seeded = await openAndSeedReport(page, ['## 현황', '', '지금은 이렇다.'].join('\n'))
  await page.getByRole('button', { name: '페이지뷰' }).click()
  await expect(page.locator('.page').first()).toBeVisible({ timeout: 30_000 })

  await page.getByRole('button', { name: '보고 문서', exact: true }).click()
  await page.getByRole('menuitemcheckbox', { name: '회의록' }).click()
  // 버튼이 고른 것을 말한다.
  await expect(page.getByRole('button', { name: '회의록', exact: true })).toBeVisible({
    timeout: 15_000,
  })

  // 그리고 문서가 그것을 들고 있다 — 이것이 없으면 화면만 바뀌고 파일은 그대로다.
  const stored = await page.evaluate(
    async ([admin, id]: [typeof E2E_ADMIN, string]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: admin.email, password: admin.password }),
      })
      const session = await login.json()
      const headers = { Authorization: `Bearer ${session.accessToken ?? session.access_token}` }
      const full = await (
        await fetch(`/api/artifacts/${id}`, { headers, credentials: 'include' })
      ).json()
      return (full.data ?? full).templateId
    },
    [E2E_ADMIN, seeded.id] as [typeof E2E_ADMIN, string],
  )
  expect(stored).toBe('doc-minutes')
})
