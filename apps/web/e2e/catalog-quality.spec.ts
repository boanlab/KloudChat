import { expect, test } from '@playwright/test'
import { signIn } from './helpers'
import { toolNames } from './composer-tools'

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

/** Image, audio and video surfaces offer no 서식 picker. */
test('이미지 표면에는 서식 고르기가 없다', async ({ page }) => {
  await page.goto('/new/image')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 20_000 })
  expect(await toolNames(page)).not.toContain('양식 고르기')
})
