import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The history screen exists for two jobs the sidebar cannot do: clearing a run
 * of test conversations, and emptying the account before handing over the
 * screen. Both are irreversible, so both are tested against what the server
 * actually holds afterwards rather than against what the list looks like.
 */
test('여러 대화를 골라 한 번에 지운다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const stamp = Date.now().toString(36)
  const titles = [`기록삭제 A ${stamp}`, `기록삭제 B ${stamp}`]
  await page.evaluate(async (names) => {
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
    for (const title of names) {
      const r = await fetch('/api/sessions', {
        method: 'POST',
        headers: H,
        body: JSON.stringify({ kind: 'chat' }),
      })
      const { id } = await r.json()
      await fetch(`/api/sessions/${id}`, { method: 'PATCH', headers: H, body: JSON.stringify({ title }) })
    }
  }, titles)

  await page.goto('/history')
  // Filter first: a "select everything visible" must not reach past what is
  // on screen.
  await page.getByLabel('대화 검색').fill(`기록삭제 A ${stamp}`)
  await page.getByRole('button', { name: '보이는 항목 전체 선택' }).click()
  await page.getByRole('button', { name: /선택 1개 삭제/ }).click()
  // Same question 모든 대화 삭제 has always asked; the picked set is just as final.
  await page.getByRole('dialog').getByRole('button', { name: '삭제', exact: true }).click()
  await expect(page.getByText(/1개의 대화를 삭제했습니다/)).toBeVisible({ timeout: 20_000 })

  // A survived, B did not — checked against the server, not the rendered list.
  const left = await page.evaluate(async (names) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = await login.json()
    const rows = await (
      await fetch('/api/sessions', { headers: { Authorization: `Bearer ${accessToken}` } })
    ).json()
    return names.filter((n) => rows.some((s: { title: string }) => s.title === n))
  }, titles)
  expect(left).toEqual([titles[1]])
})

test('모든 대화 삭제는 확인을 거치고, 아티팩트는 남긴다', async ({ page }) => {
  test.setTimeout(180_000)
  // A throwaway account of its own. Run against the shared one this would empty
  // the conversations every other spec reaches for — a test that passes by
  // breaking its neighbours.
  const owner = {
    email: `e2e-history-${Date.now().toString(36)}@example.com`,
    password: 'history-playwright-pass',
    name: '기록 삭제 확인용',
  }
  await signIn(page)
  await page.evaluate(async (account) => {
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
      body: JSON.stringify(account),
    })
    const users = await (await fetch('/api/admin/users', { headers: H })).json()
    const row = (users.items ?? users).find((u: any) => u.email === account.email)
    await fetch(`/api/admin/users/${row.id}/approve`, {
      method: 'POST',
      headers: H,
      body: JSON.stringify({ monthlyCredits: 1000 }),
    })
  }, owner)

  // Sign in as the throwaway account in the browser.
  await page.evaluate(() => fetch('/api/auth/logout', { method: 'POST' }))
  await page.goto('/')
  await page.getByLabel('이메일').fill(owner.email)
  await page.getByLabel('비밀번호').fill(owner.password)
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/auth/login') && r.request().method() === 'POST'),
    page.locator('form').getByRole('button', { name: '로그인' }).click(),
  ])
  // Signed in and inside the app shell. Read off the sidebar toggle rather
  // than a sidebar link: the sidebar itself is a drawer below 1024px, and what
  // is being checked here is the login, not the navigation.
  await expect(page.getByRole('button', { name: '사이드바 토글' })).toBeVisible({ timeout: 20_000 })

  // Two conversations and one artifact, so "what survives" has something to say.
  await page.evaluate(async () => {
    for (let i = 0; i < 2; i++) {
      await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ kind: 'chat' }),
      })
    }
  })
  const seeded = await page.evaluate(async (account) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: account.email, password: account.password }),
    })
    const { accessToken } = await login.json()
    const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` }
    for (let i = 0; i < 2; i++) {
      await fetch('/api/sessions', { method: 'POST', headers: H, body: JSON.stringify({ kind: 'chat' }) })
    }
    await fetch('/api/artifacts', {
      method: 'POST',
      headers: H,
      body: JSON.stringify({ kind: 'code', title: '남아야 하는 코드', data: { kind: 'code', content: 'print(1)', language: 'python' } }),
    })
    const sessions = await (await fetch('/api/sessions', { headers: H })).json()
    return sessions.length
  }, owner)
  expect(seeded).toBeGreaterThan(0)

  await page.goto('/history')
  // Scoped to the dialog: the page itself also warns that deleting is
  // permanent, and an unscoped match resolves to both.
  const dialog = page.getByRole('dialog')
  await page.getByRole('button', { name: '모든 대화 삭제' }).click()
  // Nothing happens until the dialog is answered.
  await expect(dialog.getByText(/되돌릴 수 없습니다/)).toBeVisible()
  await dialog.getByRole('button', { name: '취소' }).click()
  await expect(dialog).toHaveCount(0)

  await page.getByRole('button', { name: '모든 대화 삭제' }).click()
  await dialog.getByRole('button', { name: '모두 삭제' }).click()
  await expect(page.getByText(/개의 대화를 삭제했습니다/)).toBeVisible({ timeout: 30_000 })

  const after = await page.evaluate(async (account) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: account.email, password: account.password }),
    })
    const { accessToken } = await login.json()
    const H = { Authorization: `Bearer ${accessToken}` }
    const sessions = await (await fetch('/api/sessions', { headers: H })).json()
    const artifacts = await (await fetch('/api/artifacts', { headers: H })).json()
    return {
      sessions: sessions.length,
      artifacts: (Array.isArray(artifacts) ? artifacts : artifacts.items).length,
    }
  }, owner)
  expect(after.sessions).toBe(0)
  // The work outlives the conversation around it.
  expect(after.artifacts).toBe(1)
})
