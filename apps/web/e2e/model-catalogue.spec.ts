import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * What the composer says when there is nothing to choose from.
 *
 * The other half of this — that a *later* failed read must not turn a complete
 * list into a warning — is a rule about store state after a refresh, and
 * driving an in-page catalogue refresh from a browser test would take a
 * harness this repo does not have. It is held by reading `loadModels`, not by
 * a spec, and that is said here rather than left to look covered.
 */
test('목록을 한 번도 받지 못하면 고를 것이 없다고 말한다', async ({ page }) => {
  test.setTimeout(120_000)
  await page.route('**/api/models', (route) => route.fulfill({ status: 503, body: '{}' }))
  await signIn(page)
  await page.goto('/new/chat')

  // Nothing arrived, so there is nothing to choose — said in the picker's own
  // place rather than leaving an empty control that looks like a choice.
  await expect(page.getByText('사용 가능한 모델 없음')).toBeVisible({ timeout: 30_000 })
})
