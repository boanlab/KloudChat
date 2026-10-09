import { expect, test } from '@playwright/test'
import { signIn } from './helpers'
import { openTemplates, toolNames } from './composer-tools'

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

/** On 보고서 the picker lists the surface's 서식, searchable. */
test('보고서 서식 고르기는 서식을 보이고 찾게 한다', async ({ page }) => {
  await page.goto('/new/report')
  await openTemplates(page)
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })

  await expect(dialog.getByText('어떤 모양으로 받을까요?')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '이 양식으로 시작' }).first()).toBeVisible({
    timeout: 20_000,
  })
  await dialog.getByLabel('양식 검색').fill('회의록')
  await expect(dialog.locator('.grid > *').filter({ hasText: '회의록' })).toBeVisible({
    timeout: 20_000,
  })
})

/** Image, audio and video 서식 were retired: those surfaces offer no picker. */
test('이미지 표면에는 서식 고르기가 없다', async ({ page }) => {
  await page.goto('/new/image')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 20_000 })
  expect(await toolNames(page)).not.toContain('양식 고르기')
})
