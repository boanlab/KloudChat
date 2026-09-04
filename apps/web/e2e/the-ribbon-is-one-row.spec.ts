import { expect, test } from '@playwright/test'
import { openAndSeedReport } from './helpers'

/** The editor's formatting tools live in the ribbon's 홈 tab, enabled on open and visibly painted. */
test('문서 수정은 리본 밖에 줄을 더 만들지 않는다', async ({ page }) => {
  test.setTimeout(400_000)
  await openAndSeedReport(page, '## 배경\n\n첫 문단입니다.\n')
  await page.setViewportSize({ width: 1440, height: 900 })
  const panel = page.locator('[data-panel="artifact"]')
  await panel.getByRole('button', { name: '문서 수정' }).click()
  await expect(panel.locator('.ProseMirror').first()).toBeVisible({ timeout: 30_000 })

  // The font picker is in the 홈 tab.
  const home = panel.getByRole('toolbar', { name: '홈' })
  await expect(home.getByLabel('서체')).toBeVisible()
  await expect(home.getByRole('button').filter({ hasText: /^웹뷰$/ })).toBeVisible()
})

test('서식 도구는 열자마자 쓸 수 있다', async ({ page }) => {
  test.setTimeout(400_000)
  await openAndSeedReport(page, '## 배경\n\n첫 문단입니다.\n')
  const panel = page.locator('[data-panel="artifact"]')
  await panel.getByRole('button', { name: '문서 수정' }).click()
  await expect(panel.locator('.ProseMirror').first()).toBeVisible({ timeout: 30_000 })
  await expect(panel.getByRole('button', { name: '굵게' })).toBeEnabled()
  await expect(panel.getByLabel('서체')).toBeEnabled()
})

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
