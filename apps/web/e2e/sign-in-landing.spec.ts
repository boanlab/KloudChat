import { expect, test, type TestInfo, type Page } from '@playwright/test'

test.use({ serviceWorkers: 'block' })
const at = '2026-10-05T00:00:00.000Z'
const member = { id: 'member-1', name: '새 사용자', email: 'new@example.test', role: 'user', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true } }

async function signedOutApp(page: Page, testInfo: TestInfo) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  let signedIn = false
  await page.context().route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = route.request().method()
    if (path === '/auth/refresh') return signedIn
      ? route.fulfill({ json: { accessToken: 'fixture', expiresIn: 3600, user: member } })
      : route.fulfill({ status: 401, json: { detail: 'not_authenticated' } })
    if (method === 'POST' && path === '/auth/login') {
      signedIn = true
      return route.fulfill({ json: { accessToken: 'fixture', expiresIn: 3600, user: member } })
    }
    if (path === '/auth/me') return route.fulfill({ json: member })
    if (path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat'], privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false } })
    if (path === '/models') return route.fulfill({ json: { models: [], defaultChatModel: '', litellmAvailable: false,
      autoRouting: { enabled: false, available: false } } })
    if (path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    if (method === 'GET') return route.fulfill({ json: [] })
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
}

test('a member signing in on an admin address lands at home, not on 권한 없음', async ({ page }, testInfo) => {
  await signedOutApp(page, testInfo)
  // The tab an admin left on the users page after approving this account.
  await page.goto('/admin/users')
  await page.getByLabel('이메일').fill(member.email)
  await page.getByLabel('비밀번호').fill('long-enough-password')
  await page.getByRole('button', { name: '로그인', exact: true }).last().click()
  await expect(page).toHaveURL(/\/$/, { timeout: 15_000 })
  await expect(page.getByText(/관리자 권한이 필요한/)).toHaveCount(0)
})
