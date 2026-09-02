import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('슬라이드를 복제하고 내용을 유지한 채 레이아웃을 바꾼다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 20_000 })
  await page.getByRole('tab', { name: '보기', exact: true }).click()

  // 장수는 장 목록 손잡이에 「현재/전체」로 적혀 있다.
  const count = page.getByRole('button', { name: '장 목록' })
  const total = async () => Number((await count.innerText()).match(/\/(\d+)/)?.[1] ?? 0)
  const before = await total()
  const title = await page.locator('[data-slide-title], h1, h2').first().innerText().catch(() => '')

  await page.getByRole('button', { name: '장 편집' }).click()
  await page.getByRole('menuitem', { name: '이 장 복제' }).click()
  await expect.poll(total).toBe(before + 1)
  await expect(page.getByText(/사본/).first()).toBeVisible()
  await expect(page.getByText(/다른 곳에서 이미 수정/)).toHaveCount(0)

  await page.getByRole('button', { name: '장 편집' }).click()
  await page.getByRole('menuitemcheckbox', { name: '카드' }).click()
  await expect(page.getByText(/다른 곳에서 이미 수정/)).toHaveCount(0)
  if (title) await expect(page.getByText(title, { exact: false }).first()).toBeVisible()
  // The menu closes before its PATCH finishes. Reopening waits for the
  // disabled trigger to become usable and proves the panel adopted the result.
  await page.getByRole('button', { name: '장 편집' }).click()
  await expect(page.getByRole('menuitemcheckbox', { name: '카드' })).toHaveAttribute('aria-checked', 'true')
  await page.keyboard.press('Escape')

  await page.reload()
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 20_000 })
  await page.getByRole('tab', { name: '보기', exact: true }).click()
  await expect(page.getByText(`${before + 1}장`, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '2번 장' }).click()
  await page.getByRole('button', { name: '장 편집' }).click()
  await expect(page.getByRole('menuitemcheckbox', { name: '카드' })).toHaveAttribute('aria-checked', 'true')
})
