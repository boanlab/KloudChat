import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** The daily chart's bars have measured height. */
test('일별 차트에 막대가 실제 높이로 그려진다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  await page.goto('/usage')
  await expect(page.getByText('일별')).toBeVisible({ timeout: 30_000 })

  const bars = page.locator('.bg-accent\\/70')
  await expect(bars.first()).toBeVisible({ timeout: 20_000 })
  const heights = await bars.evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height))
  expect(heights.length).toBeGreaterThan(0)
  // The tallest bar fills most of the 7rem box.
  expect(Math.max(...heights)).toBeGreaterThan(20)
})
