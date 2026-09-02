import { expect, test } from '@playwright/test'
import { openAndSeedReport } from './helpers'

/**
 * 편집기를 열어도 줄이 늘지 않는다.
 *
 * Pressing 문서 수정 used to stack a fourth bar under the panel header, the
 * ribbon tabs and a ribbon row holding two buttons — and the row that was
 * actually the toolbar was the one that did not look like part of the ribbon.
 * The formatting bar belongs in the 홈 tab, which is where a word processor
 * keeps it.
 */
test('문서 수정은 리본 밖에 줄을 더 만들지 않는다', async ({ page }) => {
  test.setTimeout(400_000)
  await openAndSeedReport(page, '## 배경\n\n첫 문단입니다.\n')
  await page.setViewportSize({ width: 1440, height: 900 })
  const panel = page.locator('[data-panel="artifact"]')
  await panel.getByRole('button', { name: '문서 수정' }).click()
  await expect(panel.locator('.ProseMirror').first()).toBeVisible({ timeout: 30_000 })

  // 서체 고르개는 리본의 홈 칸 안에 있다.
  const home = panel.getByRole('toolbar', { name: '홈' })
  await expect(home.getByLabel('서체')).toBeVisible()
  await expect(home.getByRole('button').filter({ hasText: /^웹뷰$/ })).toBeVisible()
})

/**
 * 도구는 살아 있는 채로 열린다. Every formatting button was disabled until a
 * paragraph took focus, so the bar opened as twenty grey buttons and the only
 * way to learn that they work was to click into the text and look again.
 */
test('서식 도구는 열자마자 쓸 수 있다', async ({ page }) => {
  test.setTimeout(400_000)
  await openAndSeedReport(page, '## 배경\n\n첫 문단입니다.\n')
  const panel = page.locator('[data-panel="artifact"]')
  await panel.getByRole('button', { name: '문서 수정' }).click()
  await expect(panel.locator('.ProseMirror').first()).toBeVisible({ timeout: 30_000 })
  await expect(panel.getByRole('button', { name: '굵게' })).toBeEnabled()
  await expect(panel.getByLabel('서체')).toBeEnabled()
})

/**
 * 리본 안에서도 켜진 버튼은 켜져 보인다.
 *
 * The ribbon flattens the buttons inside it to look like ribbon commands, and
 * it did that by wiping every background — including a primary button's fill,
 * which left its white label on a white panel. The button was there, focusable
 * and clickable, and invisible.
 */
test('리본의 켜진 버튼은 배경을 잃지 않는다', async ({ page }) => {
  test.setTimeout(400_000)
  await openAndSeedReport(page, '## 배경\n\n첫 문단입니다.\n')
  const panel = page.locator('[data-panel="artifact"]')
  await panel.getByRole('button', { name: '문서 수정' }).click()
  const webView = panel.getByRole('button').filter({ hasText: /^웹뷰$/ })
  await expect(webView).toBeVisible({ timeout: 30_000 })
  const paint = await webView.evaluate((el) => {
    const cs = getComputedStyle(el)
    return { bg: cs.backgroundColor, fg: cs.color }
  })
  expect(paint.bg).not.toBe('rgba(0, 0, 0, 0)')
  expect(paint.bg).not.toBe('transparent')
  expect(paint.bg).not.toBe(paint.fg)
})
