import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * 관리자 › 사용자 against 120 fixture accounts: server-side search, tab filter, sort and paging,
 * with tab, search, sort and page kept in the address.
 */

type Row = {
  id: string; email: string; name: string; role: 'user' | 'admin'; status: 'pending' | 'active' | 'suspended'
  monthlyCredits: number; creditsUsed: number; cycleResetsAt: string | null; avatarColor: string
  litellmKeyPreview: string | null; litellmKeyIssuedAt: string | null; allowedModels: string[]
  createdAt: string; lastActiveAt: string | null; emailVerifiedAt: string | null
  preferences: Record<string, unknown>
}

const base = Date.parse('2026-10-01T00:00:00.000Z')
const SURNAMES = ['김', '이', '박', '최', '정', '강', '조', '윤']

function people(): Row[] {
  return Array.from({ length: 120 }, (_, i) => ({
    id: `u${String(i).padStart(3, '0')}`,
    email: `member${String(i).padStart(3, '0')}@${i % 3 ? 'lab' : 'corp'}.example`,
    name: `${SURNAMES[i % SURNAMES.length]}회원 ${String(i).padStart(3, '0')}`,
    role: i === 0 ? 'admin' : 'user',
    status: i % 10 === 5 ? 'pending' : i % 15 === 7 ? 'suspended' : 'active',
    monthlyCredits: 500_000,
    creditsUsed: ((i * 37) % 100) * 1000,
    // Far off: a reset already past makes the app re-read the account (`/auth/me`).
    cycleResetsAt: '2099-11-01T00:00:00.000Z',
    avatarColor: '#64748b',
    litellmKeyPreview: '…abcd',
    litellmKeyIssuedAt: null,
    allowedModels: [],
    createdAt: new Date(base + i * 3_600_000).toISOString(),
    lastActiveAt: i % 9 === 4 ? null : new Date(base + ((i * 13) % 120) * 3_600_000).toISOString(),
    emailVerifiedAt: null,
    preferences: { autoMemory: false, showUsage: false, streamResponses: true },
  }))
}

const RANK = { pending: 0, active: 1, suspended: 2 } as const

/** The server's order: the column, then fixed tie-breakers, never-active last. */
function ordered(rows: Row[], sort: string, order: string): Row[] {
  const dir = order === 'desc' ? -1 : 1
  const byName = (a: Row, b: Row) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)
  return [...rows].sort((a, b) => {
    if (sort === 'name') return dir * a.name.localeCompare(b.name) || a.id.localeCompare(b.id)
    if (sort === 'credits') return dir * (a.creditsUsed - b.creditsUsed) || byName(a, b)
    if (sort === 'last_active') {
      if (!a.lastActiveAt || !b.lastActiveAt) return Number(!a.lastActiveAt) - Number(!b.lastActiveAt) || byName(a, b)
      return dir * a.lastActiveAt.localeCompare(b.lastActiveAt) || byName(a, b)
    }
    return dir * (RANK[a.status] - RANK[b.status]) || b.createdAt.localeCompare(a.createdAt) || a.id.localeCompare(b.id)
  })
}

const admin = people()[0]

async function fixture(page: Page, info: TestInfo) {
  const origin = new URL(String(info.project.use.baseURL)).origin
  const rows = people()
  const unexpected: string[] = []
  const asked: URLSearchParams[] = []
  const roles: string[] = []
  const state = { lastAdmin: false, delay: 0 }
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600, user: admin })
    if (method === 'GET' && path === '/auth/config') return json({ brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false })
    if (method === 'GET' && path === '/admin/users/page') {
      const p = url.searchParams
      asked.push(p)
      if (state.delay) await new Promise((done) => setTimeout(done, state.delay))
      const q = (p.get('q') ?? '').toLowerCase()
      const status = p.get('status') ?? 'all'
      const searched = rows.filter((r) => !q || r.name.toLowerCase().includes(q) || r.email.includes(q))
      const count = (s: string) => searched.filter((r) => r.status === s).length
      const inTab = searched.filter((r) => status === 'all' || r.status === status)
      const sorted = ordered(inTab, p.get('sort') ?? 'status', p.get('order') ?? 'asc')
      const pageNo = Number(p.get('page') ?? 1)
      const limit = Number(p.get('limit') ?? 50)
      return json({
        items: sorted.slice((pageNo - 1) * limit, pageNo * limit),
        total: inTab.length, page: pageNo, limit,
        counts: { all: searched.length, pending: count('pending'), active: count('active'), suspended: count('suspended') },
        summary: {
          users: rows.length, pending: rows.filter((r) => r.status === 'pending').length,
          creditsGranted: rows.reduce((s, r) => s + r.monthlyCredits, 0),
          creditsUsed: rows.reduce((s, r) => s + r.creditsUsed, 0), nextReset: '2099-11-01T00:00:00.000Z',
        },
      })
    }
    const role = /^\/admin\/users\/([^/]+)\/role$/.exec(path)
    if (method === 'POST' && role) {
      const row = rows.find((r) => r.id === role[1])!
      const next = (request.postDataJSON() as { role: Row['role'] }).role
      roles.push(`${row.id}=${next}`)
      // The server's last-admin guard; `lastAdmin` stands for another admin demoted meanwhile.
      const others = rows.filter(
        (r) => r.role === 'admin' && r.status === 'active' && r.id !== row.id,
      )
      if (next === 'user' && (state.lastAdmin || others.length === 0)) {
        return json({ detail: 'last_admin' }, 400)
      }
      row.role = next
      return json(row)
    }
    const overview = /^\/admin\/users\/([^/]+)\/overview$/.exec(path)
    if (method === 'GET' && overview) {
      const row = rows.find((r) => r.id === overview[1])
      if (!row) return json({ detail: 'user_not_found' }, 404)
      return json({ user: row, sessions: 42, projects: 3, files: 17, artifacts: 9, nextReset: '2026-11-30T15:00:00.000Z' })
    }
    if (method === 'GET' && path === '/admin/audit') {
      const account = url.searchParams.get('account') ?? ''
      const row = rows.find((r) => r.email === account)
      return json({
        items: row ? [
          { id: 'a1', at: '2026-11-14T03:00:00.000Z', actor: admin.email, action: 'credits.set', target: row.email,
            detail: '', ip: '10.0.0.1', region: '', userAgent: '', severity: 'info', metadata: null },
          { id: 'a2', at: '2026-11-13T09:00:00.000Z', actor: row.email, action: 'login', target: row.email,
            detail: 'success', ip: '10.0.0.2', region: '', userAgent: '', severity: 'info', metadata: null },
        ] : [],
        total: row ? 2 : 0, page: 1, size: Number(url.searchParams.get('size') ?? 50),
      })
    }
    if (method === 'GET' && path === '/admin/models') return json([])
    if (method === 'GET' && path === '/admin/settings') return json({ credits: { perUsd: 100000, budgetHeadroom: 0.2 } })
    if (method === 'GET' && (path === '/admin/skills' || path === '/admin/user-pipelines')) return json([])
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (method === 'GET' && path === '/models') return json({ litellmAvailable: true, defaultChatModel: '', models: [],
      autoRouting: { enabled: false, available: false, reason: null, economyModelIds: [] } })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/sessions', '/projects', '/agents', '/skills', '/connectors', '/artifacts', '/designs',
      '/design-templates', '/memory', '/connectors/store', '/tools'].includes(path)) {
      return json([])
    }
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'Unmocked fixture request' }, 501)
  })
  return { rows, unexpected, asked, roles, state }
}

const table = (page: Page) => page.getByRole('table')
const header = (page: Page, name: string) => table(page).getByRole('columnheader', { name })
const firstNames = (page: Page, n = 3) =>
  table(page).locator('tbody tr').evaluateAll(
    (trs, count) => trs.slice(0, count).map((tr) => tr.querySelector('td p')?.firstChild?.textContent ?? ''),
    n,
  )
const range = (page: Page) => page.getByTestId('users-range')
/** A row's less common action: its 「…」 menu, then the item. */
async function menuAction(page: Page, name: string, item: string) {
  await page.getByRole('button', { name: `${name} 관리 메뉴` }).first().click()
  return page.getByRole('menuitem', { name: `${name} ${item}` })
}

test('each column heading sorts the list both ways, by mouse and by keyboard', async ({ page }, info) => {
  const { rows, unexpected, asked } = await fixture(page, info)
  await page.goto('/admin/users')
  await expect(range(page)).toHaveText('1–50 / 총 120명', { timeout: 15_000 })
  // The list opens as it always read: pending first, newest sign-up first.
  await expect(header(page, '상태')).toHaveAttribute('aria-sort', 'ascending')
  for (const name of ['사용자', '이번 달 크레딧', '마지막 활동']) {
    await expect(header(page, name)).toHaveAttribute('aria-sort', 'none')
  }
  expect(await firstNames(page)).toEqual(ordered(rows, 'status', 'asc').slice(0, 3).map((r) => r.name))

  const cases: [string, string, string][] = [
    ['사용자', 'name', 'asc'],
    ['이번 달 크레딧', 'credits', 'desc'],
    ['마지막 활동', 'last_active', 'desc'],
    ['상태', 'status', 'asc'],
  ]
  for (const [label, sort, first] of cases) {
    const second = first === 'asc' ? 'desc' : 'asc'
    await header(page, label).getByRole('button').click()
    await expect(header(page, label)).toHaveAttribute('aria-sort', first === 'asc' ? 'ascending' : 'descending')
    await expect.poll(() => firstNames(page)).toEqual(ordered(rows, sort, first).slice(0, 3).map((r) => r.name))
    expect(asked.at(-1)?.get('sort')).toBe(sort)
    expect(asked.at(-1)?.get('order')).toBe(first)
    // The same heading again, from the keyboard: the other way.
    await header(page, label).getByRole('button').focus()
    await page.keyboard.press('Enter')
    await expect(header(page, label)).toHaveAttribute('aria-sort', second === 'asc' ? 'ascending' : 'descending')
    await expect.poll(() => firstNames(page)).toEqual(ordered(rows, sort, second).slice(0, 3).map((r) => r.name))
    expect(new URL(page.url()).searchParams.get('order')).toBe(second)
  }
  // Never-active accounts stay last whichever way 마지막 활동 runs.
  await header(page, '마지막 활동').getByRole('button').click()
  await page.getByRole('button', { name: '3쪽' }).click()
  await expect(range(page)).toHaveText('101–120 / 총 120명')
  const lastRow = table(page).locator('tbody tr').last()
  await expect(lastRow).toContainText('—')
  expect(unexpected).toEqual([])
})

test('the pager moves 50 at a time and the address keeps tab, search, sort and page', async ({ page }, info) => {
  const { rows, unexpected, asked } = await fixture(page, info)
  await page.goto('/admin/users')
  await expect(range(page)).toHaveText('1–50 / 총 120명', { timeout: 15_000 })
  const pager = page.getByRole('navigation', { name: '사용자 목록 페이지' })
  await expect(pager.getByRole('button', { name: '이전 페이지' })).toBeDisabled()
  await expect(table(page).locator('tbody tr')).toHaveCount(50)

  await pager.getByRole('button', { name: '다음 페이지' }).click()
  await expect(range(page)).toHaveText('51–100 / 총 120명')
  await expect(pager.getByRole('button', { name: '2쪽' })).toHaveAttribute('aria-current', 'page')
  expect(new URL(page.url()).searchParams.get('page')).toBe('2')
  expect(await firstNames(page, 1)).toEqual([ordered(rows, 'status', 'asc')[50].name])

  await pager.getByRole('button', { name: '3쪽' }).click()
  await expect(range(page)).toHaveText('101–120 / 총 120명')
  await expect(table(page).locator('tbody tr')).toHaveCount(20)
  await expect(pager.getByRole('button', { name: '다음 페이지' })).toBeDisabled()

  // 뒤로 returns to the page before.
  await page.goBack()
  await expect(range(page)).toHaveText('51–100 / 총 120명')

  // A new sort starts again at page 1.
  await header(page, '사용자').getByRole('button').click()
  await expect(range(page)).toHaveText('1–50 / 총 120명')
  expect(new URL(page.url()).searchParams.get('page')).toBeNull()
  await pager.getByRole('button', { name: '2쪽' }).click()
  await expect(range(page)).toHaveText('51–100 / 총 120명')

  // A search starts again at page 1; the tabs count what the search finds.
  const search = page.getByRole('textbox', { name: '사용자 검색' })
  await search.fill('lab')
  await expect(range(page)).toHaveText('1–50 / 총 80명')
  const params = new URL(page.url()).searchParams
  expect([params.get('q'), params.get('page'), params.get('sort')]).toEqual(['lab', null, 'name'])
  const lab = rows.filter((r) => r.email.includes('lab'))
  await expect(page.getByRole('tab', { name: /^전체/ })).toHaveText(`전체${lab.length}`)
  await expect(page.getByRole('tab', { name: /^승인 대기/ }))
    .toHaveText(`승인 대기${lab.filter((r) => r.status === 'pending').length}`)
  // The cards above count the whole instance.
  await expect(page.getByText('전체 사용자').locator('..')).toContainText('120')

  await pager.getByRole('button', { name: '2쪽' }).click()
  await expect(range(page)).toHaveText('51–80 / 총 80명')
  // A tab starts again at page 1 and keeps the search.
  await page.getByRole('tab', { name: /^활성/ }).click()
  const activeLab = lab.filter((r) => r.status === 'active')
  await expect(range(page)).toHaveText(`1–${Math.min(50, activeLab.length)} / 총 ${activeLab.length}명`)
  expect(asked.at(-1)?.toString()).toContain('status=active')
  expect(asked.at(-1)?.get('q')).toBe('lab')
  await pager.getByRole('button', { name: '2쪽' }).click()
  await expect(range(page)).toHaveText(`51–${activeLab.length} / 총 ${activeLab.length}명`)

  // A reload keeps all of it.
  const before = new URL(page.url()).search
  await page.reload()
  await expect(range(page)).toHaveText(`51–${activeLab.length} / 총 ${activeLab.length}명`, { timeout: 15_000 })
  expect(new URL(page.url()).search).toBe(before)
  await expect(search).toHaveValue('lab')
  await expect(page.getByRole('tab', { name: /^활성/ })).toHaveAttribute('aria-selected', 'true')
  await expect(header(page, '사용자')).toHaveAttribute('aria-sort', 'ascending')
  await expect(page.getByRole('navigation', { name: '사용자 목록 페이지' }).getByRole('button', { name: '2쪽' }))
    .toHaveAttribute('aria-current', 'page')

  // An address past the end shows the last page.
  await page.goto('/admin/users?page=9')
  await expect(range(page)).toHaveText('101–120 / 총 120명', { timeout: 15_000 })
  expect(new URL(page.url()).searchParams.get('page')).toBe('3')
  expect(unexpected).toEqual([])
})

test('the pager fits a phone width', { tag: '@phone' }, async ({ page }, info) => {
  const { unexpected } = await fixture(page, info)
  await page.goto('/admin/users?page=2')
  await expect(range(page)).toHaveText('51–100 / 총 120명', { timeout: 15_000 })
  const pager = page.getByRole('navigation', { name: '사용자 목록 페이지' })
  await pager.scrollIntoViewIfNeeded()
  const width = page.viewportSize()!.width
  const box = await pager.boundingBox()
  expect(box && box.x).toBeGreaterThanOrEqual(0)
  expect(box && box.x + box.width).toBeLessThanOrEqual(width)
  for (const button of await pager.getByRole('button').all()) {
    const b = await button.boundingBox()
    expect(b && b.x + b.width).toBeLessThanOrEqual(width)
  }
  const tabs = await page.getByRole('tablist').first().boundingBox()
  expect(tabs && tabs.x + tabs.width).toBeLessThanOrEqual(width)
  // The page itself does not scroll sideways; only the table does.
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await pager.getByRole('button', { name: '3쪽' }).click()
  await expect(range(page)).toHaveText('101–120 / 총 120명')
  expect(unexpected).toEqual([])
})


test('admin is granted and revoked after a confirmation; a last-admin refusal is shown', async ({
  page,
}, info) => {
  const { rows, unexpected, roles, state } = await fixture(page, info)
  const target = rows.find((r) => r.status === 'active' && r.role === 'user')!
  await page.goto(`/admin/users?q=${target.email}`)
  await expect(range(page)).toHaveText('1–1 / 총 1명', { timeout: 15_000 })
  // One's own row cannot be demoted from here.
  await page.goto(`/admin/users?q=${admin.email}`)
  await expect(await menuAction(page, admin.name, '관리자 해제')).toBeDisabled()
  await page.keyboard.press('Escape')

  await page.goto(`/admin/users?q=${target.email}`)
  await (await menuAction(page, target.name, '관리자로 지정')).click()
  const grant = page.getByRole('dialog', { name: `${target.name}님을 관리자로 지정할까요?` })
  await grant.getByRole('button', { name: '취소' }).click()
  expect(roles).toEqual([])
  await (await menuAction(page, target.name, '관리자로 지정')).click()
  await grant.getByRole('button', { name: '관리자로 지정' }).click()
  await expect(table(page).getByText('관리자', { exact: true })).toBeVisible()
  expect(roles).toEqual([`${target.id}=admin`])

  // Revoking while another admin was demoted meanwhile: the server refuses, the reason shows.
  state.lastAdmin = true
  await (await menuAction(page, target.name, '관리자 해제')).click()
  await page.getByRole('dialog').getByRole('button', { name: '관리자 해제' }).click()
  await expect(page.getByRole('alert')).toContainText('마지막 관리자는 해제할 수 없습니다')
  expect(roles).toEqual([`${target.id}=admin`, `${target.id}=user`])
  await expect(table(page).getByText('관리자', { exact: true })).toBeVisible()

  state.lastAdmin = false
  await (await menuAction(page, target.name, '관리자 해제')).click()
  await page.getByRole('dialog').getByRole('button', { name: '관리자 해제' }).click()
  await expect(table(page).getByText('관리자', { exact: true })).toHaveCount(0)
  expect(unexpected).toEqual([])
})

const shots = process.env.SCREEN_SHOTS
/** The whole page at once: the app scrolls inside its own panes, so the window grows to fit them. */
async function wholePage(page: Page, path: string) {
  const size = page.viewportSize()!
  const height = await page.evaluate(() => Math.max(
    document.documentElement.scrollHeight,
    ...[...document.querySelectorAll('*')].filter((el) => {
      const style = getComputedStyle(el)
      return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 4
    }).map((el) => el.scrollHeight + el.getBoundingClientRect().top),
  ))
  await page.setViewportSize({ width: size.width, height: Math.min(Math.ceil(height) + 40, 5000) })
  await page.waitForTimeout(300)
  await page.screenshot({ path })
  await page.setViewportSize(size)
}
async function shot(page: Page, name: string, target?: import('@playwright/test').Locator | 'full') {
  if (!shots) return
  const path = `${shots}/admin/${name}-${page.viewportSize()?.width ?? 1440}.png`
  if (target === 'full') return void (await wholePage(page, path))
  if (target) {
    await target.scrollIntoViewIfNeeded()
    return void (await target.screenshot({ path }))
  }
  await page.screenshot({ path })
}

test('switching tabs keeps the cards and the rows until the next tab answers', { tag: '@phone' }, async ({ page }, info) => {
  const { state, unexpected } = await fixture(page, info)
  await page.goto('/admin/users')
  await expect(range(page)).toHaveText('1–50 / 총 120명', { timeout: 15_000 })
  const cards = page.getByText('전체 사용자').locator('..')
  await expect(cards).toContainText('120')
  state.delay = 1500
  await page.getByRole('tab', { name: /^활성/ }).click()
  // while the next tab loads: the instance figures and the previous rows stay
  await expect(cards).toContainText('120')
  await expect(page.getByText('다음 리필').locator('..')).not.toContainText('—')
  await expect(page.getByTestId('users-skeleton')).toHaveCount(0)
  const phone = (page.viewportSize()?.width ?? 1440) < 640
  await expect(phone ? page.locator('[data-user-card]') : table(page).locator('tbody tr')).toHaveCount(50)
  await shot(page, 'users-tab-loading')
  await expect(range(page)).toContainText('/ 총', { timeout: 15_000 })
  expect(unexpected).toEqual([])
})

test('a row keeps 크레딧 in view and labels every other action in its menu', async ({ page }, info) => {
  const { rows, unexpected } = await fixture(page, info)
  const target = rows.find((r) => r.status === 'active' && r.role === 'user')!
  await page.goto(`/admin/users?q=${target.email}`)
  await expect(range(page)).toHaveText('1–1 / 총 1명', { timeout: 15_000 })
  const row = table(page).locator('tbody tr').first()
  // a 크레딧 button and one 「…」, no bare icons
  await expect(row.getByRole('button')).toHaveCount(4) // name, copy, 크레딧, 「…」
  await row.getByRole('button', { name: `${target.name} 관리 메뉴` }).click()
  const menu = page.getByRole('menu')
  for (const item of ['상세 보기', '정보 수정', '모델 제한', '키 관리', '관리자로 지정', '정지', '계정 삭제']) {
    await expect(menu.getByRole('menuitem').filter({ hasText: new RegExp(`^${item}$`) })).toBeVisible()
  }
  await page.waitForTimeout(300) // the menu fades in
  await shot(page, 'users-row-menu')
  expect(unexpected).toEqual([])
})

test('a name opens the account in detail: credits, holdings, recent trail and its actions', async ({ page }, info) => {
  const { rows, unexpected } = await fixture(page, info)
  const target = rows.find((r) => r.status === 'active' && r.role === 'user' && r.creditsUsed > 0)!
  await page.goto(`/admin/users?q=${target.email}`)
  await expect(range(page)).toHaveText('1–1 / 총 1명', { timeout: 15_000 })
  await table(page).getByRole('button', { name: target.name, exact: true }).click()
  const detail = page.getByRole('dialog', { name: target.name })
  await expect(detail.getByTestId('user-detail')).toBeVisible()
  expect(new URL(page.url()).searchParams.get('user')).toBe(target.id)
  const facts = detail.getByTestId('user-facts')
  await expect(facts).toContainText('대화42')
  await expect(facts).toContainText('파일17')
  await expect(facts).toContainText('2026년 12월 1일')
  await expect(detail).toContainText(`${target.creditsUsed.toLocaleString()} / 500,000`)
  const trail = detail.getByTestId('user-trail')
  await expect(trail.getByRole('listitem')).toHaveCount(2)
  await expect(trail).toContainText('크레딧 변경')
  await expect(detail.getByRole('link', { name: '감사 로그에서 모두 보기' }))
    .toHaveAttribute('href', `/admin/governance?tab=log&account=${encodeURIComponent(target.email)}`)
  await expect(detail.getByRole('button', { name: '크레딧' })).toBeVisible()
  await shot(page, 'user-detail')
  // the address reopens it
  await page.reload()
  await expect(page.getByRole('dialog', { name: target.name }).getByTestId('user-facts')).toContainText('대화42', { timeout: 15_000 })
  await page.getByRole('dialog', { name: target.name }).getByRole('button', { name: '닫기' }).first().click()
  expect(new URL(page.url()).searchParams.get('user')).toBeNull()
  expect(unexpected).toEqual([])
})

test('on a phone each account is a card whose actions are in reach', { tag: '@phone' }, async ({ page }, info) => {
  const { rows, unexpected } = await fixture(page, info)
  const target = rows.find((r) => r.status === 'active' && r.role === 'user')!
  for (const width of [390, 360]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto(`/admin/users?q=${target.email}`)
    await expect(range(page)).toHaveText('1–1 / 총 1명', { timeout: 15_000 })
    await expect(page.getByRole('table')).toBeHidden()
    const card = page.locator(`[data-user-card="${target.id}"]`)
    await card.scrollIntoViewIfNeeded()
    for (const control of [card.getByRole('button', { name: '크레딧' }), card.getByRole('button', { name: `${target.name} 관리 메뉴` })]) {
      const box = (await control.boundingBox())!
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(width)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await shot(page, 'users-cards', 'full')
    await shot(page, 'users-card', card)
    await card.getByRole('button', { name: `${target.name} 관리 메뉴` }).click()
    const item = page.getByRole('menuitem', { name: `${target.name} 키 관리` })
    await expect(item).toBeVisible()
    await page.waitForTimeout(300)
    await shot(page, 'users-card-menu')
    await item.click()
    await expect(page.getByRole('dialog')).toBeVisible()
  }
  expect(unexpected.filter((u) => !u.includes('/keys'))).toEqual([])
})
