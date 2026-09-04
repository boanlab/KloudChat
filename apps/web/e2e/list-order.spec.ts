import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/** Toggling a row must not re-rank it, although lists sort newest-first and a toggle is a write. */

/** The rows on screen, in order, named by their switch. */
async function order(page: Page): Promise<string[]> {
  const names = await page.getByRole('switch').evaluateAll((els) =>
    els.map((el) => el.getAttribute('aria-label') ?? ''),
  )
  return names.filter((n) => n.endsWith('활성화') || n.endsWith('설치 상태'))
}

for (const [label, path] of [
  ['스킬', '/skills'],
  ['에이전트', '/agents'],
] as const) {
  test(`${label} 카드는 켜고 꺼도 자리를 지킨다`, async ({ page }) => {
    await signIn(page)
    await page.goto(path)
    if (label === '에이전트' && (await page.getByRole('switch').count()) === 0) {
      await page.getByRole('tab', { name: /스토어/ }).click()
      const installs = page.getByRole('button', { name: '가져오기' })
      for (let i = 0; i < Math.min(2, await installs.count()); i += 1) {
        await installs.first().click()
      }
      await page.getByRole('tab', { name: /내 에이전트/ }).click()
    }
    // Wait for the list, or two empty arrays compare equal.
    await expect(page.getByRole('switch').first()).toBeVisible({ timeout: 20_000 })

    const before = await order(page)
    expect(before.length, `${label}이 두 개는 있어야 순서를 볼 수 있습니다`).toBeGreaterThan(1)

    // The last card: a re-rank is invisible at the top.
    const target = before[before.length - 1]
    const toggle = page.getByRole('switch', { name: target })
    const was = await toggle.getAttribute('aria-checked')

    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', was === 'true' ? 'false' : 'true')
    expect(await order(page), '켠 뒤 순서가 바뀌었습니다').toEqual(before)

    // And back.
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', was ?? 'true')
    expect(await order(page), '되돌린 뒤 순서가 바뀌었습니다').toEqual(before)

    // And after the round trip to the server.
    await page.waitForTimeout(1200)
    expect(await order(page), '저장이 끝난 뒤 순서가 바뀌었습니다').toEqual(before)
  })
}
