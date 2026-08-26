import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The administrator's table on a screen it does not fit.
 *
 * `/admin/users` is the only place a pending signup is approved, a limit is
 * set, or an account is suspended — and all of that lives in the last column.
 * The table is `w-full` inside a card, so on a narrow screen the columns keep
 * their widths and the card clips whatever does not fit. Measured at 390px:
 * every one of the 32 controls in that table sat outside the viewport, 승인
 * among them, with `overflow-x: hidden` above them and no way to scroll to it.
 *
 * Asserted as *a person can reach it*, not as *a click succeeds*. A box with
 * `overflow: hidden` still scrolls programmatically, so Playwright's own
 * scroll-into-view would click a button nobody could have got to — the test
 * would pass and the administrator would still be stuck. What decides it is
 * whether a scrollable ancestor exists at all.
 *
 * `settings/access` already does this correctly, with a comment saying why, so
 * the shape is the repository's own rather than a new idea.
 */

const PHONE = { width: 390, height: 844 }
const TABLET = { width: 820, height: 1180 }

/**
 * What a person can actually get to.
 *
 * Measured on the controls rather than on the table: a `w-full` table does not
 * overflow itself, it grows, and the clipping happens on the card above it —
 * so `table.scrollWidth` reads the same as its client width and says nothing.
 * The buttons are the thing somebody is trying to press.
 */
async function reachOf(page: import('@playwright/test').Page, viewport: number) {
  return page.evaluate((width) => {
    const table = document.querySelector('table')
    if (!table) return { table: false } as const
    const controls = [...table.querySelectorAll('button')]
    const past = controls.filter((el) => el.getBoundingClientRect().right > width)
    let scrolls = false
    let overflow = 'visible'
    for (let node = table.parentElement; node; node = node.parentElement) {
      const ox = getComputedStyle(node).overflowX
      if (ox === 'auto' || ox === 'scroll') {
        scrolls = node.scrollWidth > node.clientWidth + 1
        overflow = ox
        break
      }
      if (ox === 'hidden' && node.scrollWidth > node.clientWidth + 1) {
        overflow = ox
        break
      }
    }
    return {
      table: true,
      controls: controls.length,
      past: past.length,
      names: past.slice(0, 4).map((el) => (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 14)),
      scrolls,
      overflow,
    } as const
  }, viewport)
}

for (const [name, size] of [
  ['phone', PHONE],
  ['tablet', TABLET],
] as const) {
  test(`사용자 관리 표의 오른쪽 끝에 ${name} 에서도 손이 닿는다`, async ({ page }) => {
    await page.setViewportSize(size)
    await signIn(page)
    await page.goto('/admin/users')

    // Named: without the role this screen never renders and the assertion below
    // would be about an empty page.
    await expect(
      page.getByPlaceholder('이름 또는 이메일'),
      '이 계정에 관리자 권한이 없습니다 — bash scripts/e2e-seed.sh 를 실행하세요',
    ).toBeVisible({ timeout: 20_000 })

    const found = await reachOf(page, size.width)
    expect(found.table, '사용자 표가 없습니다').toBe(true)
    if (!('past' in found) || found.past === 0) {
      // Everything is on screen at this width. Nothing to reach.
      test.skip(true, `${size.width}px 에서 표가 다 들어갑니다`)
    }
    expect(
      found.scrolls,
      `표 안 컨트롤 ${'controls' in found ? found.controls : 0}개 중 ` +
        `${'past' in found ? found.past : 0}개가 화면 밖입니다` +
        `${'names' in found ? ` (${found.names.join(', ')})` : ''} — ` +
        `overflow-x: ${'overflow' in found ? found.overflow : '?'}, 손이 닿지 않습니다`,
    ).toBe(true)
  })
}
