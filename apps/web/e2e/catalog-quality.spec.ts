import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

/** On 보고서 the picker lists the surface's 서식, searchable. */
test('보고서 서식 고르기는 서식을 보이고 찾게 한다', async ({ page }) => {
  await page.goto('/new/report')
  await page.getByRole('button', { name: '서식 고르기' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })

  await expect(dialog.getByText('어떤 모양으로 받을까요?')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '이 서식으로 시작' }).first()).toBeVisible({
    timeout: 20_000,
  })
  await dialog.getByLabel('서식 검색').fill('회의록')
  await expect(dialog.locator('.grid > *').filter({ hasText: '회의록' })).toBeVisible({
    timeout: 20_000,
  })
})

/** Image, audio and video 서식 were retired: those surfaces offer no picker. */
test('이미지 표면에는 서식 고르기가 없다', async ({ page }) => {
  await page.goto('/new/image')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('button', { name: '서식 고르기' })).toHaveCount(0)
})
