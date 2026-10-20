import { expect, test } from '@playwright/test'
import { composerFixture } from './composer-fixture'
import { setWebSearch } from './composer-tools'

/**
 * Home and composer at reach: surfaces an admin switches on appear without a reload, the
 * composer shows where the keyboard is, a chip's × is a finger-sized target, and a short
 * screen (200% zoom, a phone on its side) keeps the examples above the docked composer.
 */

test.use({ serviceWorkers: 'block' })

const chips = (page: import('@playwright/test').Page) =>
  page.getByRole('group', { name: '만들 것' }).getByRole('button')

test('a surface switched on elsewhere shows on the open home screen when the tab is back in view', { tag: '@phone' }, async ({ page }, info) => {
  const kinds = ['chat', 'report', 'slides']
  const { unexpected } = await composerFixture(page, info, { kinds })
  await page.goto('/')
  await expect(chips(page)).toHaveText(['챗', '보고서', '슬라이드'])

  // An admin turns image on in another window; this tab comes back into view.
  kinds.push('image')
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'))
    window.dispatchEvent(new Event('focus'))
  })
  await expect(chips(page)).toHaveText(['챗', '보고서', '슬라이드', '이미지'])
  expect(unexpected).toEqual([])
})

test('the composer draws a focus ring when the keyboard reaches the prompt', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  await page.goto('/')
  const input = page.getByLabel('프롬프트 입력')
  await expect(input).toBeVisible()
  const box = input.locator('xpath=ancestor::div[contains(@class, "rounded-panel")][1]')
  const ring = () => box.evaluate((el) => getComputedStyle(el).boxShadow)
  // Home focuses the prompt on arrival; measure from a blurred box.
  await input.blur()
  await expect.poll(() => input.evaluate((el) => el === document.activeElement)).toBe(false)
  const before = await ring()
  await input.focus()
  await expect(input).toBeFocused()
  await expect.poll(ring).not.toBe(before)
  expect(unexpected).toEqual([])
})

test('a chip × is at least 24px square', { tag: '@phone' }, async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  await page.goto('/')
  await setWebSearch(page, '항상')
  const remove = page.getByRole('button', { name: '웹 검색 자동으로 되돌리기' })
  await expect(remove).toBeVisible()
  const size = await remove.boundingBox()
  expect(size!.width).toBeGreaterThanOrEqual(24)
  expect(size!.height).toBeGreaterThanOrEqual(24)
  expect(unexpected).toEqual([])
})

for (const [name, viewport] of [
  ['200% zoom', { width: 640, height: 450 }],
  ['a phone on its side', { width: 844, height: 390 }],
] as const) {
  test(`at ${name} the first examples sit above the composer dock`, { tag: '@phone' }, async ({ page }, info) => {
    await page.setViewportSize(viewport)
    const { unexpected } = await composerFixture(page, info)
    await page.goto('/')
    const dock = page.getByTestId('home-dock')
    await expect(dock).toBeVisible()
    // The greeting stays the page's heading for screen readers.
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1)
    const examples = page.getByRole('region', { name: '예시' }).getByRole('button')
    const dockTop = (await dock.boundingBox())!.y
    const first = (await examples.first().boundingBox())!
    expect(first.y + first.height).toBeLessThanOrEqual(dockTop)
    expect(unexpected).toEqual([])
  })
}
