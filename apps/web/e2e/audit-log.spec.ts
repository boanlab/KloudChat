import { expect, test, type Page } from '@playwright/test'
import { viewport } from './project'

const now = '2026-10-09T03:00:00.000Z'
const shots = process.env.AUDIT_LOG_SCREENSHOT_DIR

async function shot(page: Page, name: string) {
  if (!shots) return
  await page.screenshot({ path: `${shots}/${viewport(test.info())}-${name}.png`, animations: 'disabled' })
}

type Row = Record<string, unknown> & { id: string; action: string; at: string; actor: string }

/** A trail with every kind of row the screen has to explain, then plenty of sign-ins. */
function trail(): Row[] {
  const at = (minutes: number) => new Date(Date.parse(now) - minutes * 60_000).toISOString()
  const base = { ip: '10.0.0.7', region: '', userAgent: 'Mozilla/5.0 Chrome/129.0 Safari/537.36', severity: 'info', detail: '', target: '', metadata: null }
  const rows: Row[] = [
    {
      ...base,
      id: 'gov',
      at: at(1),
      actor: 'admin@example.com',
      action: 'governance.update',
      target: '정책',
      detail: 'adaptive_classifier_model_id, adaptive_economy_model_ids, adaptive_quality_enabled, allow_user_raw_external',
      metadata: {
        changes: {
          adaptive_classifier_model_id: [null, 'strict-local/classifier'],
          adaptive_economy_model_ids: [['external/economy-a'], ['external/economy-b', 'external/economy-a']],
          adaptive_quality_enabled: [false, true],
          allow_user_raw_external: [true, false],
        },
      },
    },
    {
      ...base,
      id: 'settings',
      at: at(2),
      actor: 'admin@example.com',
      action: 'settings.update',
      target: '시스템 설정',
      detail: 'signup_mode,signup_verify_email,smtp_password',
      metadata: {
        sections: ['mail', 'signup'],
        changes: {
          signup_mode: ['approval', 'open'],
          signup_verify_email: ['', 'true'],
          smtp_password: ['••••', '••••'],
        },
      },
    },
    {
      ...base,
      id: 'legacy',
      at: at(3),
      actor: 'admin@example.com',
      action: 'settings.update',
      target: 'litellm',
      detail: 'signup_mode,signup_domains,signup_verify_email',
    },
    {
      ...base,
      id: 'role',
      at: at(4),
      actor: 'admin@example.com',
      action: 'user.role',
      target: 'hong@example.com',
      detail: 'role=admin',
      metadata: { changes: { role: ['user', 'admin'] } },
    },
    {
      ...base,
      id: 'credits',
      at: at(5),
      actor: 'admin@example.com',
      action: 'credits.set',
      target: 'hong@example.com',
      detail: 'monthly=5000',
      metadata: { changes: { monthly_credits: [1000, 5000] } },
    },
    { ...base, id: 'flow', at: at(6), actor: 'admin@example.com', action: 'pipeline.approve', target: '주간 보고 워크플로', metadata: { changes: { review: ['pending', 'approved'] } } },
    { ...base, id: 'fail', at: at(7), actor: '시스템', action: 'login', target: 'evil@example.com', detail: 'failed', severity: 'warn' },
  ]
  for (let i = 0; i < 130; i += 1) {
    rows.push({ ...base, id: `login-${i}`, at: at(10 + i * 7), actor: 'hong@example.com', action: 'login', target: 'hong@example.com' })
  }
  return rows
}

async function mockApp(page: Page) {
  const rows = trail()
  const requests: URLSearchParams[] = []
  // Every API call stays in this fixture; what the screen does not need is refused.
  await page.route('**/api/**', (route) => route.abort('blockedbyclient'))
  for (const endpoint of [/\/api\/sessions$/, /\/api\/projects$/, /\/api\/skills$/, /\/api\/memory$/, /\/api\/agents$/]) {
    await page.route(endpoint, (route) => route.fulfill({ json: [] }))
  }
  await page.route(/\/api\/auth\/refresh$/, (route) =>
    route.fulfill({
      json: {
        accessToken: 'mock-access-token',
        expiresIn: 3_600,
        user: {
          id: 'adm', email: 'admin@example.com', name: '관리자', role: 'admin', status: 'active',
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
  await page.route(/\/api\/admin\/models$/, (route) =>
    route.fulfill({
      json: [
        { id: 'strict-local/classifier', label: 'Mock · 분류기', kinds: ['chat'] },
        { id: 'external/economy-a', label: 'Mock · Economy A', kinds: ['chat'] },
        { id: 'external/economy-b', label: 'Mock · Economy B', kinds: ['chat'] },
      ],
    }),
  )
  await page.route(/\/api\/admin\/governance$/, (route) =>
    route.fulfill({
      json: {
        piiMasking: false, externalDataGuard: false, allowUserRawExternal: false, privacySafeModelIds: [],
        intentFilter: false, blockedCategories: [], retentionDays: 0, idleTimeoutMinutes: 0, internalDataStrictLocal: false,
      },
    }),
  )
  await page.route(/\/api\/admin\/audit(\?.*)?$/, (route) => {
    const params = new URL(route.request().url()).searchParams
    requests.push(params)
    const page_ = Number(params.get('page') ?? 1)
    const size = Number(params.get('size') ?? 50)
    const codes = (params.get('action') ?? '').split(',').filter(Boolean)
    const matches = (code: string) =>
      !codes.length || codes.some((c) => (c.endsWith('.*') ? code.startsWith(c.slice(0, -1)) : c === code))
    const picked = rows.filter((r) => matches(r.action))
    route.fulfill({
      json: { items: picked.slice((page_ - 1) * size, page_ * size), total: picked.length, page: page_, size },
    })
  })
  return requests
}

test('감사 로그는 무엇이 바뀌었는지 한국어로 보여 주고 쪽으로 나눈다', { tag: '@phone' }, async ({ page }) => {
  const requests = await mockApp(page)
  await page.goto('/admin/governance')
  await page.getByRole('tab', { name: /감사 로그/ }).click()

  await expect(page.getByTestId('audit-range')).toHaveText('1–50 / 전체 137건')
  await expect(page.getByRole('tab', { name: /감사 로그/ })).toContainText('137')
  const visibleRows = page.locator('[data-testid="audit-row"]:visible')
  await expect(visibleRows).toHaveCount(50)
  // Codes and raw field names never reach the screen.
  const body = page.locator('main')
  await expect(body).not.toContainText('governance.update')
  await expect(body).not.toContainText('adaptive_classifier_model_id')
  await expect(body).not.toContainText('litellm')
  await expect(visibleRows.first()).toContainText('정책 변경')
  await expect(visibleRows.first()).toContainText('난이도 분류 모델')
  await expect(visibleRows.nth(1)).toContainText('시스템 설정 · 메일 발송 · 가입')
  await expect(visibleRows.nth(2)).toContainText('시스템 설정 · 가입')
  await expect(visibleRows.nth(6)).toContainText('로그인 실패')

  // 「무엇이 바뀌었나」: item · before → after, model ids as their names, secrets masked.
  await visibleRows.first().getByRole('button', { expanded: false }).click()
  const changes = page.locator('[data-testid="audit-changes"]:visible').first()
  await expect(changes).toContainText('무엇이 바뀌었나')
  await expect(changes).toContainText('Mock · 분류기')
  await expect(changes).toContainText('Mock · Economy B, Mock · Economy A')
  await expect(changes).toContainText('사용자 원문 외부 전송 허용')
  await expect(changes).not.toContainText('strict-local/classifier')
  await visibleRows.nth(1).getByRole('button', { expanded: false }).click()
  const settings = page.locator('[data-testid="audit-changes"]:visible').nth(1)
  await expect(settings).toContainText('바로 사용')
  await expect(settings).toContainText('새 값으로 바뀜 (값은 기록하지 않음)')
  await visibleRows.nth(2).getByRole('button', { expanded: false }).click()
  await expect(page.locator('[data-testid="audit-changes"]:visible').nth(2)).toContainText('가입 가능한 이메일 도메인')
  await visibleRows.first().scrollIntoViewIfNeeded()
  await shot(page, 'expanded')

  // Paging.
  await page.getByRole('button', { name: '다음 페이지' }).click()
  await expect(page.getByTestId('audit-range')).toHaveText('51–100 / 전체 137건')
  await page.getByRole('button', { name: '3쪽' }).click()
  await expect(page.getByTestId('audit-range')).toHaveText('101–137 / 전체 137건')
  await page.getByTestId('audit-range').scrollIntoViewIfNeeded()
  await shot(page, 'pages')
  await expect(page.getByRole('button', { name: '다음 페이지' })).toBeDisabled()
  await page.getByLabel('페이지당 건수').selectOption('100')
  await expect(page.getByTestId('audit-range')).toHaveText('1–100 / 전체 137건')

  // Filters go to the server.
  await page.getByLabel('행위 종류').selectOption({ label: '시스템 설정 · 정책' })
  await expect(page.getByTestId('audit-range')).toHaveText('1–3 / 전체 3건')
  expect(requests.at(-1)?.get('action')).toContain('governance.*')
  await page.getByLabel('계정', { exact: true }).fill('hong')
  await expect.poll(() => requests.at(-1)?.get('account')).toBe('hong')
  await page.getByLabel('기간').selectOption('7d')
  await expect.poll(() => Boolean(requests.at(-1)?.get('since'))).toBe(true)

  // Nothing runs off the page.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})

test('감사 로그 첫 화면', { tag: '@phone' }, async ({ page }) => {
  await mockApp(page)
  await page.goto('/admin/governance')
  await page.getByRole('tab', { name: /감사 로그/ }).click()
  await expect(page.getByTestId('audit-range')).toHaveText('1–50 / 전체 137건')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await shot(page, 'list')
})
