import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The daily chart has to draw.
 *
 * A percentage height resolves against a parent with a height, and a flex item
 * aligned to the end is only as tall as its content — so the bars measured
 * zero and the card rendered as an empty box with a title on it. Nothing about
 * that shows in a snapshot of the markup; only a measured height catches it.
 */
test('일별 차트에 막대가 실제 높이로 그려진다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  await page.goto('/usage')
  await expect(page.getByText('일별')).toBeVisible({ timeout: 30_000 })

  const bars = page.locator('.bg-accent\\/70')
  await expect(bars.first()).toBeVisible({ timeout: 20_000 })
  const heights = await bars.evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height))
  expect(heights.length).toBeGreaterThan(0)
  // The tallest is the peak day and fills most of the 7rem box.
  expect(Math.max(...heights)).toBeGreaterThan(20)
})
