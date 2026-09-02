/**
 * 서식을 고르는 자리: 스크롤 뒤에 숨지 않는다.
 *
 * This used to be a rail on the home screen — two rows of four, because before
 * that it was a horizontal scroller and the cards past the fold sat behind a
 * gesture people do not make, so half of what was on offer was never seen.
 *
 * The rail is gone: 서식 folded into 작업 시작하기, which is one dialogue per
 * surface. The lesson did not go with it. Whatever holds the cards has to show
 * them without a sideways gesture, and there has to be a way to the whole list.
 */
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('작업 시작하기의 카드는 가로로 숨지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  // 보고서에서는 카드가 일이고, 서식은 그 일이 데려온다.
  await expect(dialog.getByRole('button', { name: /시작점 선택$/ }).first()).toBeVisible({
    timeout: 20_000,
  })

  // Nothing scrolls sideways: that was the whole problem with the rail.
  const sideways = await dialog.evaluate((root) => {
    let worst = 0
    for (const node of Array.from(root.querySelectorAll<HTMLElement>('*'))) {
      const style = getComputedStyle(node)
      if (style.overflowX !== 'auto' && style.overflowX !== 'scroll') continue
      worst = Math.max(worst, node.scrollWidth - node.clientWidth)
    }
    return worst
  })
  expect(sideways, '서식 목록이 가로로 스크롤합니다').toBeLessThanOrEqual(1)

  // More than fits on one page is reached by paging, which is visible.
  const pager = dialog.getByText(/\d+\s*\/\s*\d+/)
  if (await pager.isVisible().catch(() => false)) {
    await expect(dialog.getByRole('button', { name: /다음|»|›/ }).first()).toBeVisible()
  }
})

test('전체 서식 목록으로 가는 길이 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  // 모두 보기 lived on the rail. The whole catalogue is its own screen now, and
  // the account menu is how somebody reaches it.
  await page.goto('/designs')
  await expect(page.getByRole('tab', { name: '서식' })).toBeVisible({ timeout: 20_000 })
  await page.getByRole('tab', { name: '서식' }).click()
  await expect(page.getByRole('button', { name: '이 서식으로 시작' }).first()).toBeVisible({
    timeout: 20_000,
  })
})
