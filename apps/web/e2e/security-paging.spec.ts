import { expect, test, type Page } from '@playwright/test'
import { viewport } from './project'

const now = '2026-10-09T03:00:00.000Z'
const shots = process.env.SECURITY_PAGING_SCREENSHOT_DIR

async function shot(page: Page, name: string) {
  if (!shots) return
  await page.screenshot({ path: `${shots}/${viewport(test.info())}-${name}.png`, fullPage: true, animations: 'disabled' })
}

const at = (minutes: number) => new Date(Date.parse(now) - minutes * 60_000).toISOString()

type SessionRow = { familyId: string; current: boolean; lastSeenAt: string; userAgent: string; ip: string; region: string }

/** 21 sign-ins: this browser (least recently active) and twenty curl and unknown ones. */
function signIns(): SessionRow[] {
  const rows: SessionRow[] = [
    { familyId: 'cur', current: true, lastSeenAt: at(600), userAgent: 'Mozilla/5.0 Chrome/129.0 Safari/537.36', ip: '10.0.0.7', region: '대한민국 용인' },
  ]
  for (let i = 0; i < 20; i += 1) {
    rows.push({ familyId: `f${i}`, current: false, lastSeenAt: at(i + 1), userAgent: i % 2 ? '' : 'curl/8.5.0', ip: `10.0.1.${i}`, region: '' })
  }
  return rows
}

async function mockApp(page: Page) {
  const sessions = signIns()
  const events = Array.from({ length: 45 }, (_, i) => ({
    id: `e${i}`, at: at(i * 30), action: i === 3 ? 'password.change' : 'login', detail: '', ip: '10.0.0.7', region: '',
    userAgent: 'Mozilla/5.0 Chrome/129.0 Safari/537.36', severity: 'info',
  }))
  const asked: { sessions: string[]; access: string[] } = { sessions: [], access: [] }

  await page.route('**/api/**', (route) => route.abort('blockedbyclient'))
  for (const endpoint of [/\/api\/sessions(\?.*)?$/, /\/api\/projects$/, /\/api\/skills$/, /\/api\/memory$/, /\/api\/agents$/]) {
    await page.route(endpoint, (route) => route.fulfill({ json: [] }))
  }
  await page.route(/\/api\/auth\/refresh$/, (route) =>
    route.fulfill({
      json: {
        accessToken: 'mock-access-token',
        expiresIn: 3_600,
        user: {
          id: 'u1', email: 'hong@example.com', name: '홍길동', role: 'user', status: 'active',
          monthlyCredits: 10_000, creditsUsed: 0, cycleResetsAt: null, avatarColor: '#64748b',
          litellmKeyPreview: null, litellmKeyIssuedAt: null,
          preferences: { streamResponses: true, autoMemory: false, showUsage: false, privacyDefaultAction: 'ask' },
          allowedModels: [], createdAt: now, lastActiveAt: now,
        },
      },
    }),
  )
  await page.route(/\/api\/auth\/config$/, (route) =>
    route.fulfill({
      json: {
        passwordResetEnabled: false, dictationEnabled: false, brand: { name: 'KloudChat', logo: '' },
        enabledKinds: ['chat'], privacy: { externalDataGuard: false, allowUserRawExternal: false },
      },
    }),
  )
  await page.route(/\/api\/models$/, (route) => route.fulfill({ json: { models: [], litellmAvailable: true, defaultChatModel: '' } }))

  // The server's order: the current sign-in first, then latest activity.
  await page.route(/\/api\/auth\/me\/sessions(\?.*)?$/, (route) => {
    const params = new URL(route.request().url()).searchParams
    asked.sessions.push(params.toString())
    const p = Number(params.get('page') ?? 1)
    const size = Number(params.get('size') ?? 10)
    const ordered = [...sessions].sort((a, b) => Number(b.current) - Number(a.current) || b.lastSeenAt.localeCompare(a.lastSeenAt))
    const items = ordered.slice((p - 1) * size, p * size).map((s) => ({ ...s, startedAt: at(900), expiresAt: at(-10_000) }))
    route.fulfill({ json: { items, total: ordered.length, others: ordered.filter((s) => !s.current).length, page: p, size } })
  })
  await page.route(/\/api\/auth\/me\/sessions\/[^/?]+$/, (route) => {
    if (route.request().method() !== 'DELETE') return route.fallback()
    const id = route.request().url().split('/').pop()
    const at_ = sessions.findIndex((s) => s.familyId === id)
    if (at_ >= 0) sessions.splice(at_, 1)
    route.fulfill({ json: { revoked: 1 } })
  })
  await page.route(/\/api\/auth\/me\/access(\?.*)?$/, (route) => {
    const params = new URL(route.request().url()).searchParams
    asked.access.push(params.toString())
    const p = Number(params.get('page') ?? 1)
    const size = Number(params.get('size') ?? 20)
    route.fulfill({ json: { items: events.slice((p - 1) * size, p * size), total: events.length, page: p, size } })
  })
  return asked
}

test('로그인된 기기는 현재 기기를 맨 앞에 두고 10개씩 나누며, 마지막 줄을 종료하면 앞 쪽으로 간다', { tag: '@phone' }, async ({ page }) => {
  const asked = await mockApp(page)
  await page.goto('/settings/access')

  const devices = page.getByTestId('active-sessions')
  const rows = devices.getByTestId('session-row')
  const nav = page.getByRole('navigation', { name: '로그인된 기기 페이지' })
  await expect(devices.getByRole('heading', { name: /로그인된 기기/ })).toHaveText('로그인된 기기21')
  await expect(rows).toHaveCount(10)
  await expect(rows.first()).toContainText('현재 기기')
  await expect(rows.nth(1)).toContainText('curl')
  await expect(nav).toContainText('1–10 / 전체 21건')
  expect(asked.sessions[0]).toBe('page=1&size=10')
  await expect(devices.getByRole('button', { name: '다른 기기 모두 로그아웃' })).toBeVisible()

  // Compact rows: 3.5rem (56px at the desktop root; the phone root is larger), nothing lost.
  const rem = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize))
  const box = await rows.nth(1).boundingBox()
  expect(box!.height).toBeLessThanOrEqual(3.5 * rem + 1)
  await expect(rows.nth(1)).toContainText('10.0.1.0')
  await expect(rows.nth(1)).toContainText('최근 사용')
  await expect(rows.nth(1).locator('time')).toHaveAttribute('title', /2026/)
  await shot(page, 'devices-page1')

  await nav.getByRole('button', { name: '다음 페이지' }).click()
  await expect(nav).toContainText('11–20 / 전체 21건')
  await expect(rows.first()).not.toContainText('현재 기기')
  await nav.getByRole('button', { name: '3쪽' }).click()
  await expect(nav).toContainText('21–21 / 전체 21건')
  await expect(rows).toHaveCount(1)
  await expect(nav.getByRole('button', { name: '다음 페이지' })).toBeDisabled()

  // Ending the only row on page 3 steps back to page 2.
  await rows.first().getByRole('button', { name: '종료' }).click()
  await page.getByRole('dialog').getByRole('button', { name: '로그아웃' }).click()
  await expect(nav).toContainText('11–20 / 전체 20건')
  await expect(nav.getByRole('button', { name: '2쪽' })).toHaveAttribute('aria-current', 'page')
  await expect(rows).toHaveCount(10)
  await expect(devices.getByRole('heading', { name: /로그인된 기기/ })).toHaveText('로그인된 기기20')
  await expect(nav.getByRole('button', { name: '3쪽' })).toHaveCount(0)
})

test('접속 기록은 20건씩 최신순으로 나눈다', async ({ page }) => {
  const asked = await mockApp(page)
  await page.goto('/settings/access')

  const log = page.getByTestId('access-log')
  const nav = page.getByRole('navigation', { name: '접속 기록 페이지' })
  const body = log.locator('tbody tr')
  await expect(body).toHaveCount(20)
  await expect(log.getByRole('heading', { name: /접속 기록/ })).toHaveText('접속 기록45')
  await expect(nav).toContainText('1–20 / 전체 45건')
  await expect(log).not.toContainText('100건')
  expect(asked.access[0]).toBe('page=1&size=20')
  await expect(body.nth(3)).toContainText('비밀번호 변경')

  await nav.getByRole('button', { name: '다음 페이지' }).click()
  await expect(nav).toContainText('21–40 / 전체 45건')
  await nav.getByRole('button', { name: '3쪽' }).click()
  await expect(nav).toContainText('41–45 / 전체 45건')
  await expect(body).toHaveCount(5)
  await expect(nav.getByRole('button', { name: '다음 페이지' })).toBeDisabled()
  await nav.getByRole('button', { name: '이전 페이지' }).click()
  await expect(nav).toContainText('21–40 / 전체 45건')
  // The other list keeps its own page.
  await expect(page.getByRole('navigation', { name: '로그인된 기기 페이지' })).toContainText('1–10 / 전체 21건')
  await nav.scrollIntoViewIfNeeded()
  await shot(page, 'access-page2')
})
