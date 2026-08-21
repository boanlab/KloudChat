import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The 관리 section belongs to administrators.
 *
 * Checked with a real account rather than a stubbed role: the menu reads
 * `user.role`, and what has to hold is that the server sends `user` for an
 * ordinary account and that nothing else on the way in promotes it.
 */
test('일반 사용자에게는 계정 메뉴에 관리 항목이 없다', async ({ page }) => {
  test.setTimeout(180_000)

  const account = {
    email: `e2e-plain-${Date.now().toString(36)}@example.com`,
    password: 'plain-playwright-pass',
    name: '일반 사용자',
  }

  // Created and approved by the administrator, which is the only way an
  // account becomes usable on an approval-mode instance.
  await signIn(page)
  await page.evaluate(async (acct) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = await login.json()
    const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` }
    await fetch('/api/auth/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(acct),
    })
    const users = await (await fetch('/api/admin/users', { headers: H })).json()
    const row = (users.items ?? users).find((u: { email: string }) => u.email === acct.email)
    await fetch(`/api/admin/users/${row.id}/approve`, {
      method: 'POST',
      headers: H,
      body: JSON.stringify({ monthlyCredits: 1000 }),
    })
  }, account)

  await page.evaluate(() => fetch('/api/auth/logout', { method: 'POST' }))
  await page.goto('/')
  await page.getByLabel('이메일').fill(account.email)
  await page.getByLabel('비밀번호').fill(account.password)
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/api/auth/login') && r.request().method() === 'POST',
    ),
    page.locator('form').getByRole('button', { name: '로그인' }).click(),
  ])
  await expect(page.getByRole('button', { name: '사이드바 토글' })).toBeVisible({ timeout: 20_000 })

  // The account menu is the whole subject: it is where 관리 lives.
  await page.getByRole('button', { name: new RegExp(account.name) }).click()
  await expect(page.getByText('AI 에이전트 연동')).toBeVisible()
  await expect(page.getByText('관리', { exact: true })).toHaveCount(0)
  await expect(page.getByText('사용자 · 크레딧')).toHaveCount(0)
  await expect(page.getByText('보안 · 감사')).toHaveCount(0)

  // And the screen itself is refused, not merely unlinked.
  await page.goto('/admin/users')
  await expect(page.getByText('사용자 · 크레딧')).toHaveCount(0)
  console.log('일반 사용자 메뉴: 관리 항목 없음, /admin/users 도 열리지 않음')
})
