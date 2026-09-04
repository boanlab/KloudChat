/** 작업 시작하기 shows its cards without sideways scrolling, and the whole 서식 list has its own screen. */
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('작업 시작하기의 카드는 가로로 숨지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  await expect(dialog.getByRole('button', { name: /시작점 선택$/ }).first()).toBeVisible({
    timeout: 20_000,
  })

  // Nothing scrolls sideways.
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

  // Overflow is paged, visibly.
  const pager = dialog.getByText(/\d+\s*\/\s*\d+/)
  if (await pager.isVisible().catch(() => false)) {
    await expect(dialog.getByRole('button', { name: /다음|»|›/ }).first()).toBeVisible()
  }
})

test('전체 서식 목록으로 가는 길이 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/designs')
  await expect(page.getByRole('tab', { name: '서식' })).toBeVisible({ timeout: 20_000 })
  await page.getByRole('tab', { name: '서식' }).click()
  await expect(page.getByRole('button', { name: '이 서식으로 시작' }).first()).toBeVisible({
    timeout: 20_000,
  })
})
