import { expect, test, type Page, type Route, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

test.use({ serviceWorkers: 'block' })

/** A session outlives its short access token: a lapsed one is renewed under the request,
 *  a refresh the network dropped keeps the person signed in, and signing out in one tab
 *  signs out the others. No backend: every call is answered here. */

const at = '2026-11-01T00:00:00.000Z'
const id = '55555555555555555555555555555555'
const member = {
  id: 'member-1', name: '갱신 확인', email: 'renew@example.test', role: 'user', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}
const session = {
  id, title: '갱신 확인 대화', kind: 'chat', model: 'fixture/model', routingMode: 'manual',
  projectId: null, agentId: null, artifactId: null, pinned: false, messages: [], messageCount: 0,
  made: null, createdAt: at, updatedAt: at,
}

interface Backend {
  /** Answers `/auth/refresh`; by default a fresh token per call. */
  refresh?: (route: Route, n: number) => Promise<void> | void
  /** Answers `POST /auth/login`. */
  login?: (route: Route) => Promise<void> | void
  /** Answers the turn's `POST /sessions/{id}/messages`. */
  send?: (route: Route) => Promise<void> | void
  /** Tokens the API still takes; any other is answered 401. */
  live?: (token: string) => boolean
}

async function app(page: Page, testInfo: TestInfo, backend: Backend = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const seen = { refreshes: 0, signedIn: true, bearers: [] as string[] }
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (path === '/auth/refresh') {
      seen.refreshes += 1
      if (!seen.signedIn) return route.fulfill({ status: 204 })
      if (backend.refresh) return backend.refresh(route, seen.refreshes)
      return route.fulfill({ json: { accessToken: `t${seen.refreshes}`, expiresIn: 900, user: member } })
    }
    if (method === 'POST' && path === '/auth/login') {
      if (backend.login) return backend.login(route)
      seen.signedIn = true
      return route.fulfill({ json: { accessToken: 'login', expiresIn: 900, user: member } })
    }
    if (method === 'POST' && path === '/auth/signup') {
      // `open` signup: the server signs the new account in and sets the refresh cookie.
      seen.signedIn = true
      return route.fulfill({ status: 201, json: {
        user: member, session: { accessToken: 'signup', expiresIn: 900, user: member },
      } })
    }
    if (method === 'POST' && path === '/auth/logout') {
      seen.signedIn = false
      return route.fulfill({ status: 204 })
    }
    if (path === '/auth/config') return route.fulfill({ json: {
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    } })
    const token = (request.headers().authorization ?? '').replace(/^Bearer /, '')
    seen.bearers.push(`${method} ${path} ${token}`)
    if (backend.live && !backend.live(token)) {
      return route.fulfill({ status: 401, json: { detail: 'token_expired' } })
    }
    if (method === 'POST' && path === `/sessions/${id}/messages` && backend.send) return backend.send(route)
    if (path === '/models') return route.fulfill({ json: {
      models: [{ id: 'fixture/model', label: 'Fixture model', name: 'Fixture model', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat'], modality: 'chat', dataBoundary: 'external', creditCost: 1,
        inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/model', litellmAvailable: false,
      autoRouting: { enabled: false, available: false },
    } })
    if (path === '/credits') {
      return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    }
    if (path === '/sessions') return route.fulfill({ json: [session] })
    if (path === `/sessions/${id}`) return route.fulfill({ json: session })
    if (path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET') return route.fulfill({ json: [] })
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  return seen
}

const accountMenu = (page: Page) =>
  page.getByRole('button', { name: /^계정 메뉴/ }).filter({ visible: true }).first()
const signInForm = (page: Page) => page.getByLabel('이메일')

test('a lapsed token is renewed once and the refused requests go through', async ({ page }, testInfo) => {
  // The first token is already dead server-side; the second one works.
  const seen = await app(page, testInfo, { live: (token) => token !== 't1' })
  await page.goto('/')
  await expect(page.getByText('갱신 확인 대화').first()).toBeVisible({ timeout: 15_000 })
  await expect(accountMenu(page)).toBeVisible()
  // Every workspace read met the dead token together; one refresh served them all.
  expect(seen.refreshes).toBe(2)
  expect(seen.bearers).toContain('GET /sessions t2')
  await expect(signInForm(page)).toHaveCount(0)
})

test('a refresh the network dropped keeps the person signed in and is tried again', async ({ page }, testInfo) => {
  const seen = await app(page, testInfo, {
    live: (token) => token !== 't1',
    // The renewal under the refused reads fails twice: once on the network, once with a 503.
    refresh: (route, n) => n === 2
      ? route.abort('failed')
      : n === 3
        ? route.fulfill({ status: 503, json: { detail: 'unavailable' } })
        : route.fulfill({ json: { accessToken: `t${n}`, expiresIn: 900, user: member } }),
  })
  await page.goto('/')
  await expect(accountMenu(page)).toBeVisible()
  // Retried on its backoff (2 s, then 5 s) until a token comes back; never signed out meanwhile.
  await expect.poll(() => seen.refreshes, { timeout: 15_000 }).toBeGreaterThanOrEqual(4)
  await expect(signInForm(page)).toHaveCount(0)
  await expect(accountMenu(page)).toBeVisible()
  // Not remembered as signed out: a reload asks the cookie again.
  expect(await page.evaluate(() => localStorage.getItem('kchat-signed-out'))).toBeNull()
})

test('signing out in one tab signs out the other, and signing in brings it back', async ({ page }, testInfo) => {
  await app(page, testInfo)
  const other = await page.context().newPage()
  await page.goto('/')
  await other.goto('/')
  await expect(accountMenu(page)).toBeVisible()
  await expect(accountMenu(other)).toBeVisible()

  await accountMenu(page).click()
  await page.getByRole('menuitem', { name: '로그아웃' }).click()
  await expect(signInForm(page)).toBeVisible()
  await expect(signInForm(other)).toBeVisible()
  await expect(other.getByText('갱신 확인 대화')).toHaveCount(0)

  await page.getByLabel('이메일').fill(member.email)
  await page.getByLabel('비밀번호').fill('long-enough-password')
  await page.getByRole('button', { name: '로그인', exact: true }).last().click()
  await expect(accountMenu(page)).toBeVisible()
  await expect(accountMenu(other)).toBeVisible()
})

test('a new account stays signed in across a reload', async ({ page }, testInfo) => {
  const seen = await app(page, testInfo)
  // This browser last signed out: the flag that spares the refresh call is set.
  seen.signedIn = false
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('signed-out-once')) {
      sessionStorage.setItem('signed-out-once', '1')
      localStorage.setItem('kchat-signed-out', '1')
    }
  })
  await page.goto('/')
  await expect(signInForm(page)).toBeVisible()
  expect(seen.refreshes).toBe(0)

  await page.getByRole('button', { name: '회원가입', exact: true }).click()
  await page.getByLabel('이름').fill(member.name)
  await page.getByLabel('이메일').fill(member.email)
  await page.getByLabel('비밀번호').fill('long-enough-password-1!')
  await page.getByRole('button', { name: /^(가입하기|가입 요청)$/ }).click()
  await expect(accountMenu(page)).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('kchat-signed-out'))).toBeNull()

  await page.reload()
  await expect(accountMenu(page)).toBeVisible()
  expect(seen.refreshes).toBeGreaterThanOrEqual(1)
})

test('a locked sign-in says how long the lock lasts', async ({ page }, testInfo) => {
  const seen = await app(page, testInfo, {
    login: (route) => route.fulfill({
      status: 429, headers: { 'Retry-After': '290' }, json: { detail: 'account_locked' },
    }),
  })
  seen.signedIn = false
  await page.goto('/')
  await page.getByLabel('이메일').fill(member.email)
  await page.getByLabel('비밀번호').fill('long-enough-password')
  await page.getByRole('button', { name: '로그인', exact: true }).last().click()
  await expect(page.getByText('로그인에 여러 번 실패해 잠시 잠겼습니다. 5분 뒤에 다시 시도하세요.')).toBeVisible()
})

test('a refused turn says when it can be sent again', async ({ page }, testInfo) => {
  await app(page, testInfo, {
    send: (route) => route.fulfill({
      status: 429, headers: { 'Retry-After': '30' }, json: { detail: 'rate_limited' },
    }),
  })
  await page.goto(`/s/${id}`)
  await page.getByLabel('프롬프트 입력').fill('다시 보낼 때를 알려 줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page.getByText(/30초 뒤에 다시 보낼 수 있습니다\./)).toBeVisible()
})

test('going offline shows a quiet notice that leaves once the network is back', async ({ page }, testInfo) => {
  await app(page, testInfo)
  await page.goto('/')
  await expect(accountMenu(page)).toBeVisible()
  await page.context().setOffline(true)
  await expect(page.getByTestId('offline-bar')).toHaveText('인터넷 연결이 끊겼습니다. 연결되면 다시 시도하세요.')
  await page.context().setOffline(false)
  await expect(page.getByTestId('offline-bar')).toHaveCount(0)
})
