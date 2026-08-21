import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('접속기록은 로그인과 실패한 시도를 남긴다', async ({ page, request }) => {
  test.setTimeout(120_000)

  // A failed attempt, so the screen has the thing it exists to show.
  await request.post('/api/auth/login', {
    data: { email: 'e2e-personas@example.com', password: 'wrong-on-purpose' },
    failOnStatusCode: false,
  })

  await signIn(page)
  await page.goto('/settings/access')

  const table = page.getByRole('table')
  await expect(table).toBeVisible({ timeout: 30_000 })
  await expect(table.getByText('로그인', { exact: true }).first()).toBeVisible()

  const first = (await table.locator('tbody tr').first().textContent())?.replace(/\s+/g, ' ').trim()
  console.log('첫 줄:', first)
  // The browser column reads as a name, not as a UA string.
  expect(first).toMatch(/Chrome|Firefox|Safari|Edge/)
  expect(first).not.toContain('Mozilla/5.0')
})
