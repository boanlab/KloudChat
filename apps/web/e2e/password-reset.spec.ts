/**
 * Password reset, end to end through a real mail server.
 *
 * Checking that the link in the mail really opens the form and really changes
 * the password means reading the mail.
 *
 * Needs an inbox on the app network:
 *
 *   docker run -d --name kchat-mailpit --network kchat_kchat -p 8025:8025 axllent/mailpit
 *
 * Without one this skips rather than fails — a missing test dependency is not
 * a defect in the product.
 */

import { expect, test } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

const MAILPIT = 'http://localhost:8025'
const ACCOUNT = {
  email: 'e2e-reset@example.com',
  password: 'reset-playwright-pass',
  name: '재설정 확인용',
}

test.describe.configure({ mode: 'serial' })

async function mailpitUp(): Promise<boolean> {
  try {
    return (await fetch(`${MAILPIT}/api/v1/messages?limit=1`)).ok
  } catch {
    return false
  }
}

test('메일 설정이 켜지면 로그인 화면에서 비밀번호를 재설정할 수 있다', async ({ page }) => {
  test.setTimeout(180_000)
  test.skip(!(await mailpitUp()), 'mailpit 이 없습니다 — 메일 경로는 실제 서버로만 검증합니다.')

  // Admin work through the API: this test is about the sign-in page, and driving
  // the settings form here would fail for reasons that belong to another test.
  await signIn(page)
  const ready = await page.evaluate(
    async ([admin, account]: any) => {
      const token = async (c: any) =>
        (
          await (
            await fetch('/api/auth/login', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ email: c.email, password: c.password }),
            })
          ).json()
        ).accessToken
      const adminToken = await token(admin)
      const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` }

      await fetch('/api/admin/settings', {
        method: 'PUT',
        headers: H,
        body: JSON.stringify({
          smtpHost: 'kchat-mailpit',
          smtpPort: '1025',
          smtpSecurity: 'none',
          smtpFrom: 'kchat <no-reply@example.com>',
          appBaseUrl: window.location.origin,
        }),
      })

      // An account that exists and is active, or the request mails nothing.
      await fetch('/api/auth/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(account),
      })
      const users = await (await fetch('/api/admin/users', { headers: H })).json()
      const row = (users.items ?? users).find((u: any) => u.email === account.email)
      if (row && row.status !== 'active') {
        await fetch(`/api/admin/users/${row.id}/approve`, {
          method: 'POST',
          headers: H,
          body: JSON.stringify({ monthlyCredits: 1000 }),
        })
      }
      return (await (await fetch('/api/auth/config')).json()).passwordResetEnabled
    },
    [E2E_ADMIN, ACCOUNT],
  )
  expect(ready, '메일 설정이 반영되지 않았습니다').toBe(true)

  const since = (await (await fetch(`${MAILPIT}/api/v1/messages?limit=1`)).json()).total as number

  // Signed out, the link is now on offer — it was not before mail was set up.
  await page.evaluate(() => fetch('/api/auth/logout', { method: 'POST' }))
  await page.goto('/')
  await page.getByRole('button', { name: '비밀번호를 잊으셨나요?' }).click()
  await page.getByLabel('이메일').fill(ACCOUNT.email)
  await page.getByRole('button', { name: '재설정 링크 받기' }).click()
  // Says nothing about whether that address is registered.
  await expect(page.getByText(/가입된 계정이 있다면/)).toBeVisible({ timeout: 20_000 })

  // Poll the mailbox rather than sleep: delivery is fast but not instant, and a
  // fixed wait is either flaky or slow.
  const readToken = async (): Promise<string | null> => {
    const inbox = await (await fetch(`${MAILPIT}/api/v1/messages?limit=20`)).json()
    if (inbox.total <= since) return null
    const row = inbox.messages.find(
      (m: any) => m.To[0].Address === ACCOUNT.email && m.Subject.includes('재설정'),
    )
    if (!row) return null
    const body = await (await fetch(`${MAILPIT}/api/v1/message/${row.ID}`)).json()
    return /token=([A-Za-z0-9_-]+)/.exec(body.Text)?.[1] ?? null
  }
  await expect
    .poll(readToken, { timeout: 30_000, message: '재설정 메일이 도착하지 않았습니다' })
    .not.toBeNull()
  const token = (await readToken())!

  const fresh = `reset-done-${Date.now().toString(36)}`
  await page.goto(`/?token=${token}`)
  await expect(page.getByRole('heading', { name: '새 비밀번호 설정' })).toBeVisible()
  await page.getByLabel('새 비밀번호').fill(fresh)
  await page.getByRole('button', { name: '비밀번호 바꾸기' }).click()

  // Signed in on the spot — the person just proved they hold the address.
  await expect(page.getByRole('link', { name: '아티팩트' })).toBeVisible({ timeout: 30_000 })
  // And the token is gone from the address bar rather than sitting in history.
  expect(page.url()).not.toContain('token=')

  // The old password no longer works; the new one does.
  const codes = await page.evaluate(
    async ([email, oldPassword, newPassword]: any) => {
      const attempt = async (password: string) =>
        (
          await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
          })
        ).status
      return { old: await attempt(oldPassword), fresh: await attempt(newPassword) }
    },
    [ACCOUNT.email, ACCOUNT.password, fresh],
  )
  expect(codes.old, '옛 비밀번호가 아직 통합니다').toBe(401)
  expect(codes.fresh).toBe(200)
})

test('메일 설정을 지우면 재설정 링크가 사라진다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.evaluate(async (admin: any) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: admin.email, password: admin.password }),
    })
    const { accessToken } = await login.json()
    await fetch('/api/admin/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
      body: JSON.stringify({ smtpHost: '' }),
    })
  }, E2E_ADMIN)

  await page.evaluate(() => fetch('/api/auth/logout', { method: 'POST' }))
  await page.goto('/')
  // No offer, and no dead link — the honest instruction instead.
  await expect(page.getByRole('button', { name: '비밀번호를 잊으셨나요?' })).toHaveCount(0)
  await expect(page.getByText('비밀번호를 잊었다면 관리자에게 문의하세요.')).toBeVisible()
})
