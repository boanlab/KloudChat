import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** With no model list ever received, the picker says so. (A later failed refresh keeping the list is `loadModels`' rule, not covered here.) */
test('목록을 한 번도 받지 못하면 고를 것이 없다고 말한다', async ({ page }) => {
  test.setTimeout(120_000)
  await page.route('**/api/models', (route) => route.fulfill({ status: 503, body: '{}' }))
  await signIn(page)
  await page.goto('/new/chat')

  await expect(page.getByText('사용 가능한 모델 없음')).toBeVisible({ timeout: 30_000 })
})
