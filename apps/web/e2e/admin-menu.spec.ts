import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, openSidebar, signIn } from './helpers'

type ListedUser = { id: string; email: string }

const ADMIN_SCREENS = [
  { path: '/admin/users', heading: '사용자 · 크레딧', api: '/api/admin/users' },
  { path: '/admin/usage', heading: '사용량', api: '/api/admin/usage?days=7' },
  { path: '/admin/system', heading: '시스템', api: '/api/admin/settings' },
  { path: '/admin/governance', heading: '보안 · 감사', api: '/api/admin/governance' },
] as const

async function createOrdinaryAccount(page: Page) {
  const account = {
    email: `e2e-plain-${Date.now().toString(36)}@example.com`,
    password: 'plain-playwright-pass',
    name: '일반 사용자',
  }

  // The shared admin must exist before its token is used.
  await signIn(page)
  const login = await page.request.post('/api/auth/login', {
    data: { email: E2E_ADMIN.email, password: E2E_ADMIN.password },
  })
  expect(login.ok(), 'E2E 관리자 로그인').toBeTruthy()
  const { accessToken } = (await login.json()) as { accessToken: string }
  const headers = { Authorization: `Bearer ${accessToken}` }

  const signup = await page.request.post('/api/auth/signup', { data: account })
  expect(signup.status(), '일반 사용자 가입').toBe(201)

  const usersResponse = await page.request.get('/api/admin/users', { headers })
  expect(usersResponse.ok(), '관리자 사용자 목록').toBeTruthy()
  const body = (await usersResponse.json()) as ListedUser[] | { items: ListedUser[] }
  const users = Array.isArray(body) ? body : body.items
  const user = users.find((candidate) => candidate.email === account.email)
  expect(user, '방금 가입한 일반 사용자').toBeTruthy()

  const approval = await page.request.post(`/api/admin/users/${user!.id}/approve`, {
    headers,
    data: { monthlyCredits: 1_000 },
  })
  expect(approval.ok(), '일반 사용자 승인').toBeTruthy()

  // Also puts the ordinary account's refresh cookie into the browser context.
  const ordinaryLogin = await page.request.post('/api/auth/login', {
    data: { email: account.email, password: account.password },
  })
  expect(ordinaryLogin.ok(), '일반 사용자 로그인').toBeTruthy()
  const ordinarySession = (await ordinaryLogin.json()) as { accessToken: string }

  return { account, accessToken: ordinarySession.accessToken }
}

/** 관리 is admin-only, checked with a real ordinary account rather than a stubbed role. */
test('일반 사용자는 관리 메뉴와 모든 관리자 deep link에서 거부된다', async ({ page }) => {
  const { account, accessToken } = await createOrdinaryAccount(page)
  await page.goto('/')
  await expect(page.getByRole('button', { name: '사이드바 토글' })).toBeVisible({ timeout: 20_000 })
  await openSidebar(page)

  // The account menu trigger's accessible name is `계정 메뉴 · <email>`.
  await page.getByRole('button', { name: `계정 메뉴 · ${account.email}` }).click()
  await expect(page.getByText('AI 에이전트 연동')).toBeVisible()
  await expect(page.getByText('관리', { exact: true })).toHaveCount(0)
  await expect(page.getByText('사용자 · 크레딧')).toHaveCount(0)
  await expect(page.getByText('보안 · 감사')).toHaveCount(0)

  // The server refuses regardless of the route guard.
  const headers = { Authorization: `Bearer ${accessToken}` }
  for (const screen of ADMIN_SCREENS) {
    const response = await page.request.get(screen.api, { headers })
    expect(response.status(), `${screen.api} 일반 사용자 응답`).toBe(403)
  }

  let adminRequests = 0
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/admin/')) adminRequests += 1
  })

  // Refusal renders at the requested URL, with no admin page mounted and no admin API call.
  for (const screen of ADMIN_SCREENS) {
    await page.goto(screen.path)
    await expect(page.getByRole('heading', { name: '이 페이지에 접근할 수 없습니다.' })).toBeVisible()
    expect(new URL(page.url()).pathname, '거부 화면이 요청 URL을 유지함').toBe(screen.path)
    await expect(page.getByRole('heading', { name: screen.heading })).toHaveCount(0)
  }
  expect(adminRequests, 'role guard 뒤 관리자 API 호출 수').toBe(0)
})

test('관리자는 모든 관리자 deep link에 접근할 수 있다', async ({ page }) => {
  await signIn(page)

  for (const screen of ADMIN_SCREENS) {
    await page.goto(screen.path)
    await expect(page.getByRole('heading', { name: screen.heading })).toBeVisible({
      timeout: 15_000,
    })
    await expect(page.getByRole('heading', { name: '이 페이지에 접근할 수 없습니다.' })).toHaveCount(0)
  }
})
