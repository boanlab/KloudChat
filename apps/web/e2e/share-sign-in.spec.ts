import { expect, test } from '@playwright/test'

/**
 * A share link made for 「계정이 있는 사람」, opened signed out: the page offers 로그인 in place
 * and, once signed in, opens the shared conversation on the same address.
 */

const at = '2026-10-09T07:00:00.000Z'
const user = {
  id: 'fixture-user', email: 'fixture@example.test', name: 'UI 검증', role: 'user', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}

test('a signed-in-only share link offers 로그인 and opens after it', async ({ page }, info) => {
  const origin = new URL(String(info.project.use.baseURL)).origin
  let signedIn = false
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (path === '/auth/refresh') return signedIn
      ? json({ accessToken: 'fixture-only', expiresIn: 3600, user })
      : json({ detail: 'not_signed_in' }, 401)
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'], privacy: { externalDataGuard: false },
      passwordResetEnabled: false, dictationEnabled: false, signupMode: 'open',
    })
    if (path === '/auth/login') {
      signedIn = true
      return json({ accessToken: 'fixture-only', expiresIn: 3600, user })
    }
    if (path === '/shared/tok123') {
      if (!route.request().headers().authorization) return json({ detail: 'signin_required' }, 401)
      return json({
        kind: 'session', title: '공유된 대화', sessionKind: 'chat', startedWith: null, artifact: null,
        messages: [
          { id: 'm1', role: 'user', content: '장학금 신청 기간 알려 줘', attachments: [], createdAt: at },
          { id: 'm2', role: 'assistant', content: '신청은 3월 2일까지입니다.', attachments: [], createdAt: at },
        ],
      })
    }
    // The app behind the login loads its workspace; empty answers are enough here.
    return json(route.request().method() === 'GET' ? [] : {})
  })
  await page.goto('/share/tok123')
  await expect(page.getByRole('heading', { name: '로그인이 필요한 링크입니다' })).toBeVisible()
  await page.getByRole('button', { name: '로그인', exact: true }).click()
  await page.locator('input[type=email]').fill('fixture@example.test')
  await page.locator('input[type=password]').fill('password-1234')
  await page.locator('input[type=password]').press('Enter')
  await expect(page).toHaveURL(/\/share\/tok123$/)
  await expect(page.getByText('신청은 3월 2일까지입니다.')).toBeVisible()
})
