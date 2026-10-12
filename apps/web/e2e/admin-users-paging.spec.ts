import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * 관리자 › 사용자 against fixture data: 120 accounts, read 50 at a time. The server searches,
 * filters by tab, sorts and pages (`GET /admin/users/page`); the screen sorts by a column
 * heading, pages below the table, and keeps tab, search, sort and page in the address.
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
    cycleResetsAt: '2026-11-01T00:00:00.000Z',
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
          creditsUsed: rows.reduce((s, r) => s + r.creditsUsed, 0), nextReset: '2026-11-01T00:00:00.000Z',
        },
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
      '/design-templates', '/memory', '/connectors/catalog', '/tools'].includes(path)) {
      return json([])
    }
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'Unmocked fixture request' }, 501)
  })
  return { rows, unexpected, asked }
}

const table = (page: Page) => page.getByRole('table')
const header = (page: Page, name: string) => table(page).getByRole('columnheader', { name })
const firstNames = (page: Page, n = 3) =>
  table(page).locator('tbody tr').evaluateAll(
    (trs, count) => trs.slice(0, count).map((tr) => tr.querySelector('td p')?.firstChild?.textContent ?? ''),
    n,
  )
const range = (page: Page) => page.getByTestId('users-range')

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

