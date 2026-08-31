/**
 * The 서식 rail on the home screen: two rows of four, most-used first.
 *
 * It was a horizontal scroller, which hid its own contents — the cards past
 * the fold sat behind a gesture people do not make, so half of what was on
 * offer was never seen. And it was ordered by template id, which is the order
 * the files happen to sit in and means nothing to anybody.
 */
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('여덟 장을 4×2 로 보여준다', async ({ page }) => {
  await signIn(page)
  await page.goto('/')
  const rail = page.getByRole('region', { name: '서식에서 시작' })
  await expect(rail).toBeVisible({ timeout: 20_000 })

  const cards = rail.locator('> div:last-child > button')
  await expect(cards).toHaveCount(8)

  // Four columns, measured rather than read off the class — a grid that has
  // collapsed to two columns still has the class that says four.
  const tops = await cards.evaluateAll((els) =>
    els.map((e) => Math.round(e.getBoundingClientRect().top)),
  )
  const rows = [...new Set(tops)]
  expect(rows).toHaveLength(2)
  expect(tops.filter((t) => t === rows[0])).toHaveLength(4)

  // Nothing scrolls sideways: that was the whole problem with the rail.
  const overflow = await rail.locator('> div:last-child').evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
})


test('오른쪽 위 모두 보기는 그대로다', async ({ page }) => {
  await signIn(page)
  await page.goto('/')
  const rail = page.getByRole('region', { name: '서식에서 시작' })
  const all = rail.getByRole('link', { name: /모두 보기/ })
  await expect(all).toBeVisible({ timeout: 20_000 })
  await expect(all).toHaveAttribute('href', /\/designs/)
})
