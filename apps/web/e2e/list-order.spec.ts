import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Switching a row on or off must not move it.
 *
 * Both lists rank newest-first so a just-created row lands where it can be
 * seen. `updatedAt` moves on every write, though, and a toggle is a write — so
 * flipping a switch re-ranked the card to the top, out from under the cursor
 * that had just left it, and flipping it back moved it again.
 */

/** The rows on screen, in order, named by their switch. */
async function order(page: Page): Promise<string[]> {
  const names = await page.getByRole('switch').evaluateAll((els) =>
    els.map((el) => el.getAttribute('aria-label') ?? ''),
  )
  return names.filter((n) => n.endsWith('활성화'))
}

for (const [label, path] of [
  ['스킬', '/skills'],
  ['에이전트', '/agents'],
] as const) {
  test(`${label} 카드는 켜고 꺼도 자리를 지킨다`, async ({ page }) => {
    await signIn(page)
    await page.goto(path)
    // The list arrives from the API after the header does; reading the order
    // before it lands would compare two empty arrays and pass.
    await expect(page.getByRole('switch').first()).toBeVisible({ timeout: 20_000 })

    const before = await order(page)
    expect(before.length, `${label}이 두 개는 있어야 순서를 볼 수 있습니다`).toBeGreaterThan(1)

    // The last card, not the first: a re-rank is invisible at the top.
    const target = before[before.length - 1]
    const toggle = page.getByRole('switch', { name: target })
    const was = await toggle.getAttribute('aria-checked')

    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', was === 'true' ? 'false' : 'true')
    expect(await order(page), '켠 뒤 순서가 바뀌었습니다').toEqual(before)

    // And back, which is the second half of the complaint: the row moved twice.
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', was ?? 'true')
    expect(await order(page), '되돌린 뒤 순서가 바뀌었습니다').toEqual(before)

    // The order also has to survive the round trip to the server, not just the
    // optimistic update in front of it.
    await page.waitForTimeout(1200)
    expect(await order(page), '저장이 끝난 뒤 순서가 바뀌었습니다').toEqual(before)
  })
}
