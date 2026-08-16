import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A surface an administrator has switched off.
 *
 * The sidebar and home screen hide these already, but the URL keeps working —
 * a bookmark, a shared link, an address-bar completion — and the composer that
 * came up looked entirely normal. Typing into it did nothing: the server
 * refuses to open the session and the refusal escaped as an unhandled promise,
 * so the screen said nothing at all.
 *
 * Skipped when every surface is on, because then there is nothing to assert.
 */
test('꺼져 있는 화면은 그렇다고 말한다', async ({ page }) => {
  await signIn(page)
  const enabled: string[] = await page.evaluate(async () => {
    // `/api/auth/config` is where a signed-in client reads this; there is no
    // `/api/settings`, and fetching it 404s into an empty list — which made
    // the skip below never fire and the assertion run against a live surface.
    const r = await fetch('/api/auth/config')
    return r.ok ? ((await r.json()).enabledKinds ?? []) : []
  })
  const off = ['image', 'av'].find((k) => !enabled.includes(k))
  test.skip(!off, '이 인스턴스는 모든 화면이 켜져 있습니다.')

  await page.goto(`/new/${off}`)
  await expect(page.getByText(/기능이 꺼져 있습니다/)).toBeVisible({ timeout: 20_000 })
  // And an exit, rather than a dead end.
  await expect(page.getByRole('button', { name: '홈으로' })).toBeVisible()
  // The composer must not be there offering something that cannot happen.
  await expect(page.getByLabel('프롬프트 입력')).toHaveCount(0)
})
