import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'
import { shellRead } from './shell-mocks'

/**
 * Commercial-standard table and admin behaviour against fixture data: every data table sorts
 * by its headings (units understood, asc → desc → unsorted, `aria-sort`, remembered), the
 * audit log and user list export CSV with the screen's filters, strict-local safe models are
 * an ordered priority list, the privacy card lets the person pick one, an unknown address
 * is a 404 page, the tab title names the screen, and an expired sign-in says so.
 */

const at = '2026-11-01T00:00:00.000Z'
const admin = {
  id: 'admin-1', email: 'admin@example.test', name: '관리자', role: 'admin', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, cycleResetsAt: '2099-11-01T00:00:00.000Z', avatarColor: '#64748b',
  allowedModels: [], createdAt: at, lastActiveAt: at, emailVerifiedAt: at, litellmKeyPreview: '…abcd',
  litellmKeyIssuedAt: null, preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}

const strict = (id: string, label: string) => ({
  id, label, name: label, vendor: 'Fixture', provider: 'fixture', kinds: ['chat'], modality: 'chat',
  dataBoundary: 'self_hosted', strictLocal: true, privacyOnly: false, creditCost: 0, inputCreditCost: 0,
  supportsTools: true, supportsVision: false, contextWindow: 32000,
})
const MODELS = [strict('strict-local/a', '로컬 A'), strict('strict-local/b', '로컬 B'), strict('strict-local/c', '로컬 C')]

const COMPONENTS = [
  // memory: 2.2 GB, 499 MB, 1.5 GB, none; network: 1.9 MB/s, 3.1 KB/s, 640 KB/s
  { name: 'api-1', mem: 2.2 * 1024 ** 3, rx: 1.9 * 1024 ** 2, cores: 0.42 },
  { name: 'web', mem: 499 * 1024 ** 2, rx: 3.1 * 1024, cores: 0.03 },
  { name: 'worker', mem: 1.5 * 1024 ** 3, rx: 640 * 1024, cores: 1.25 },
  { name: 'print', mem: null, rx: null, cores: null },
].map((c) => ({
  name: c.name, cpuPercent: null, cpuCores: c.cores, cpuLimitPercent: null, cpuCoresPeak: c.cores,
  memBytes: c.mem, memLimitPercent: null, memPeak: c.mem, netRx: c.rx, netTx: c.rx, ioRead: null, ioWrite: null,
}))

function overview() {
  return {
    generatedAt: at, range: '1h', stepSec: 60, status: 'ok', verdicts: [],
    collector: { enabled: true, lastSampleAt: at, hostAvailable: true, modelLogAt: null, targets: [] },
    host: { cpuPercent: 10, cpus: 8, memPercent: 40, memUsed: 1, memTotal: 2, load1: 0.5, netRx: 1, netTx: 1,
      cpuPeak: 20, memPeak: 41, disks: [], devices: [] },
    components: COMPONENTS, models: [], vllm: [], gpus: [], replicas: [], pipeline: [], charts: [],
    thresholds: { waitingMax: 4, kvCacheMax: 90, ttftP95MaxSec: 5, apiCpuMax: 0.8, memMax: 90, diskWarn: 80,
      diskCritical: 90, errorRateMax: 5, gpuTempMax: 85, gpuNodeMemMax: 90, sustainMin: 5, apiCpuSustainMin: 10 },
  }
}

const USAGE = {
  days: 7, since: at,
  totals: { credits: 900, requests: 30, searches: 3, activeUsers: 3, allocatedCredits: 3000, otherCredits: 0 },
  daily: [{ date: '2026-11-01', credits: 900, requests: 30, searches: 3 }],
  byModel: [{ model: 'strict-local/a', credits: 900, requests: 30, users: 3 }],
  bySurface: [{ kind: 'chat', credits: 900, requests: 30 }],
  topUsers: [
    { id: 'u1', name: '김하나', email: 'one@example.test', credits: 500, requests: 10, searches: 1, allowance: 1000 },
    { id: 'u2', name: '이두리', email: 'two@example.test', credits: 300, requests: 15, searches: 2, allowance: 1000 },
    { id: 'u3', name: '박세찌', email: 'three@example.test', credits: 100, requests: 5, searches: 0, allowance: 0 },
  ],
}
const STORAGE = {
  path: '/data', usedBytes: 1, files: 1, diskTotalBytes: 100 * 1024 ** 3, diskFreeBytes: 50 * 1024 ** 3,
  reclaimAt: 0, orphanBytes: 0, orphanFiles: 0,
  byUser: [
    { id: 'u1', name: '김하나', email: 'one@example.test', bytes: 499 * 1024 ** 2, files: 3 },
    { id: 'u2', name: '이두리', email: 'two@example.test', bytes: 2.2 * 1024 ** 3, files: 9 },
    { id: 'u4', name: '최네시', email: 'four@example.test', bytes: 12 * 1024, files: 1 },
  ],
}

const AUDIT = Array.from({ length: 3 }, (_, i) => ({
  id: `ev${i}`, at: `2026-11-0${i + 1}T00:00:00.000Z`, actor: 'admin@example.test',
  action: ['login', 'user.approve', 'credits.set'][i], target: 'someone@example.test', detail: '', metadata: null,
  ip: `10.0.0.${i + 1}`, region: '', userAgent: 'Mozilla/5.0 Chrome/120', severity: 'info',
}))

type Fixture = {
  unexpected: string[]
  governance: Record<string, unknown>
  puts: Record<string, unknown>[]
  auditAsks: URLSearchParams[]
  exports: string[]
  decided: string[]
  state: { expired: boolean }
}

const pendingUser = (id: string, name: string) => ({ ...admin, id, name, email: `${id}@example.test`, role: 'user',
  status: 'pending', lastActiveAt: null, emailVerifiedAt: null })

async function adminFixture(page: Page, info: TestInfo): Promise<Fixture> {
  const origin = new URL(String(info.project.use.baseURL)).origin
  const fx: Fixture = {
    unexpected: [], puts: [], auditAsks: [], exports: [], decided: [], state: { expired: false },
    governance: {
      piiMasking: false, externalDataGuard: true, internalDataStrictLocal: true, allowUserRawExternal: false,
      privacySafeModelIds: ['strict-local/c', 'strict-local/a'], intentFilter: false, blockedCategories: [],
      retentionDays: 0, idleTimeoutMinutes: 0, adaptiveRoutingEnabled: false, adaptiveClassifierModelId: null,
      adaptiveEconomyModelIds: [], adaptiveQualityEnabled: false, adaptiveQualityModelIds: [], outlineModelId: null,
    },
  }
  let refreshes = 0
  const people = [admin, pendingUser('p1', '대기 하나'), pendingUser('p2', '대기 둘')]
  page.on('pageerror', (error) => fx.unexpected.push(`pageerror: ${error.message}`))
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (path === '/auth/refresh') {
      refreshes += 1
      if (fx.state.expired) return json({ detail: 'refresh_expired' }, 401)
      return json({ accessToken: `fixture-${refreshes}`, expiresIn: 3600, user: admin })
    }
    if (fx.state.expired && path !== '/auth/config') return json({ detail: 'token_expired' }, 401)
    if (method === 'GET' && path === '/auth/config') return json({ brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat'], privacy: { externalDataGuard: true }, passwordResetEnabled: false, dictationEnabled: false })
    if (method === 'GET' && path === '/models') return json({ litellmAvailable: true, defaultChatModel: 'strict-local/a',
      models: MODELS, autoRouting: { enabled: false, available: false, reason: null, economyModelIds: [] } })
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (method === 'GET' && path === '/admin/perf/overview') return json(overview())
    if (method === 'GET' && path === '/admin/usage') return json(USAGE)
    if (method === 'GET' && path === '/admin/storage') return json(STORAGE)
    if (method === 'GET' && path === '/admin/governance') return json(fx.governance)
    if (method === 'PUT' && path === '/admin/governance') {
      const patch = request.postDataJSON() as Record<string, unknown>
      fx.puts.push(patch)
      Object.assign(fx.governance, patch)
      return json({ ok: true, clearedMessages: 0 })
    }
    if (method === 'GET' && path === '/admin/audit') {
      fx.auditAsks.push(url.searchParams)
      const sort = url.searchParams.get('sort') ?? 'at'
      const dir = url.searchParams.get('order') === 'asc' ? 1 : -1
      const key = sort as 'at' | 'action' | 'ip'
      const items = [...AUDIT].sort((a, b) => String(a[key]).localeCompare(String(b[key])) * dir)
      return json({ items, total: items.length, page: 1, size: 50 })
    }
    if (method === 'GET' && (path === '/admin/audit/export.csv' || path === '/admin/users/export.csv')) {
      fx.exports.push(`${path}?${url.searchParams}`)
      return route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/csv; charset=utf-8',
          'Content-Disposition': `attachment; filename*=UTF-8''${path.includes('audit') ? 'kloudchat-audit-20261113.csv' : 'kloudchat-users-20261113.csv'}`,
        },
        body: '﻿name,email\r\n',
      })
    }
    const decide = /^\/admin\/users\/([^/]+)\/(approve|reject)$/.exec(path)
    if (method === 'POST' && decide) {
      fx.decided.push(`${decide[2]} ${decide[1]}`)
      const row = people.find((p) => p.id === decide[1])!
      row.status = decide[2] === 'approve' ? 'active' : 'suspended'
      return json(row)
    }
    if (method === 'GET' && path === '/admin/users/page') {
      const count = (s: string) => people.filter((p) => p.status === s).length
      return json({ items: people, total: people.length, page: 1, limit: 50,
        counts: { all: people.length, pending: count('pending'), active: count('active'), suspended: count('suspended') },
        summary: { users: 1, pending: 0, creditsGranted: 1000, creditsUsed: 0, nextReset: '2099-11-01T00:00:00.000Z' } })
    }
    if (method === 'GET' && path === '/admin/models') return json(MODELS.map(({ id, label, kinds }) => ({ id, label, kinds })))
    if (method === 'GET' && path === '/admin/settings') return json({ credits: { perUsd: 100000, budgetHeadroom: 0.2 } })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    if (method === 'GET' && ['/sessions', '/projects', '/agents', '/skills', '/connectors', '/artifacts', '/designs',
      '/design-templates', '/memory', '/connectors/catalog', '/tools', '/admin/skills', '/admin/user-pipelines',
      '/jobs', '/user-pipelines', '/shares'].includes(path)) return json([])
    fx.unexpected.push(`${method} ${path}`)
    return json({ detail: 'Unmocked fixture request' }, 501)
  })
  return fx
}

const header = (table: Locator, name: string) => table.getByRole('columnheader', { name })
const column = (table: Locator, n: number) =>
  table.locator('tbody tr').evaluateAll((rows, i) => rows.map((r) => r.children[i]?.textContent?.trim() ?? ''), n)

test('the host components table sorts by size and rate, cycles back to unsorted, and remembers it @phone', async ({ page }, info) => {
  const fx = await adminFixture(page, info)
  await page.goto('/admin/performance/host')
  const table = page.getByRole('table', { name: '컴포넌트' })
  await expect(table.locator('tbody tr')).toHaveCount(4, { timeout: 15_000 })
  const memory = header(table, '메모리').first()
  await expect(memory).toHaveAttribute('aria-sort', 'none')
  // Figures open largest first; "2.2 GB" above "1.5 GB" above "499 MB", empty last.
  await memory.getByRole('button').click()
  await expect(memory).toHaveAttribute('aria-sort', 'descending')
  expect(await column(table, 0)).toEqual(['api-1', 'worker', 'web', 'print'])
  await memory.getByRole('button').click()
  await expect(memory).toHaveAttribute('aria-sort', 'ascending')
  expect(await column(table, 0)).toEqual(['web', 'worker', 'api-1', 'print'])
  await memory.getByRole('button').click()
  await expect(memory).toHaveAttribute('aria-sort', 'none')
  expect(await column(table, 0)).toEqual(['api-1', 'web', 'worker', 'print'])

  // A rate: "1.9 MB/s" above "640 KB/s" above "3.1 KB/s"; kept after a reload.
  const network = header(table, '네트워크 ↓/↑')
  await network.getByRole('button').focus()
  await page.keyboard.press('Enter')
  expect(await column(table, 0)).toEqual(['api-1', 'worker', 'web', 'print'])
  await page.reload()
  await expect(header(page.getByRole('table', { name: '컴포넌트' }), '네트워크 ↓/↑')).toHaveAttribute('aria-sort', 'descending', { timeout: 15_000 })

  // A narrow screen scrolls the table inside its box, never the page.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  expect(fx.unexpected).toEqual([])
})

test('the usage per-user table sorts by storage and filters by name', async ({ page }, info) => {
  const fx = await adminFixture(page, info)
  await page.goto('/admin/usage')
  const table = page.getByRole('table', { name: '사용자별' })
  await expect(table.locator('tbody tr')).toHaveCount(4, { timeout: 15_000 })
  // Opens most credits first.
  await expect(header(table, '크레딧')).toHaveAttribute('aria-sort', 'descending')
  await header(table, '저장 용량').getByRole('button').click()
  const names = await column(table, 0)
  expect(names.map((n) => n.slice(0, 3))).toEqual(['이두리', '김하나', '최네시', '박세찌'])
  await page.getByRole('searchbox', { name: '사용자 검색' }).fill('two@')
  await expect(table.locator('tbody tr')).toHaveCount(1)
  expect(fx.unexpected).toEqual([])
})

test('the audit log sorts on the server and exports the same filters and order as CSV', async ({ page }, info) => {
  const fx = await adminFixture(page, info)
  await page.goto('/admin/governance')
  await page.getByRole('tab', { name: /감사 로그/ }).click()
  const table = page.locator('table').filter({ has: page.getByRole('columnheader', { name: '행위' }) })
  await expect(table.locator('tbody tr')).toHaveCount(3, { timeout: 15_000 })
  await expect(header(table, '시각')).toHaveAttribute('aria-sort', 'descending')
  await header(table, '접속 위치').getByRole('button').click()
  await expect.poll(() => fx.auditAsks.at(-1)?.get('sort')).toBe('ip')
  expect(fx.auditAsks.at(-1)?.get('order')).toBe('asc')
  await expect(header(table, '접속 위치')).toHaveAttribute('aria-sort', 'ascending')
  await page.getByRole('combobox', { name: '행위 종류' }).selectOption({ index: 1 })
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'CSV 내보내기' }).click()
  expect((await download).suggestedFilename()).toBe('kloudchat-audit-20261113.csv')
  const asked = new URLSearchParams(fx.exports.at(-1)!.split('?')[1])
  expect(asked.get('sort')).toBe('ip')
  expect(asked.get('order')).toBe('asc')
  expect(asked.get('action')).toBeTruthy()
  expect(asked.get('page')).toBeNull()
  expect(fx.unexpected).toEqual([])
})

test('the user list exports CSV under its tab, search and order', async ({ page }, info) => {
  const fx = await adminFixture(page, info)
  await page.goto('/admin/users?tab=active&sort=credits&order=desc')
  await expect(page.getByTestId('users-range')).toBeVisible({ timeout: 15_000 })
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'CSV 내보내기' }).click()
  expect((await download).suggestedFilename()).toBe('kloudchat-users-20261113.csv')
  expect(fx.exports).toEqual(['/admin/users/export.csv?status=active&sort=credits&order=desc'])
  await expect(page).toHaveTitle('사용자 · 크레딧 · KloudChat')
  expect(fx.unexpected).toEqual([])
})

test('strict-local safe models are an ordered priority list the admin moves', async ({ page }, info) => {
  const fx = await adminFixture(page, info)
  await page.goto('/admin/governance')
  const order = page.getByTestId('safe-model-order')
  await expect(order.getByRole('listitem')).toHaveText([/로컬 C/, /로컬 A/], { timeout: 15_000 })
  await expect(page.getByText('이 목록의 첫 번째 모델을 씁니다', { exact: false })).toBeVisible()
  // Move A up: the stored order follows.
  await page.getByRole('button', { name: '로컬 A 위로' }).click()
  await expect.poll(() => fx.puts.at(-1)?.privacySafeModelIds).toEqual(['strict-local/a', 'strict-local/c'])
  await expect(order.getByRole('listitem')).toHaveText([/로컬 A/, /로컬 C/])
  await expect(page.getByRole('button', { name: '로컬 A 위로' })).toBeDisabled()
  // Checking B adds it last; dragging it to the top puts it first.
  await page.getByRole('checkbox', { name: '로컬 B' }).check()
  await expect.poll(() => fx.puts.at(-1)?.privacySafeModelIds).toEqual(['strict-local/a', 'strict-local/c', 'strict-local/b'])
  await order.getByRole('listitem').nth(2).dragTo(order.getByRole('listitem').nth(0))
  await expect.poll(() => fx.puts.at(-1)?.privacySafeModelIds).toEqual(['strict-local/b', 'strict-local/a', 'strict-local/c'])
  // Unchecking takes it out of the order.
  await page.getByRole('checkbox', { name: '로컬 A' }).uncheck()
  await expect.poll(() => fx.puts.at(-1)?.privacySafeModelIds).toEqual(['strict-local/b', 'strict-local/c'])
  await expect(order.getByRole('listitem')).toHaveText([/로컬 B/, /로컬 C/])
  expect(fx.unexpected).toEqual([])
})

test('an unknown address is a 404 page with a way home, and the tab names the screen', async ({ page }, info) => {
  const fx = await adminFixture(page, info)
  await page.goto('/no-such-screen')
  await expect(page.getByTestId('not-found')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('heading', { name: '이 주소에는 화면이 없습니다.' })).toBeVisible()
  await expect(page).toHaveTitle('페이지를 찾을 수 없음 · KloudChat')
  await page.getByRole('button', { name: '홈으로' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page).toHaveTitle('새 대화 · KloudChat')
  expect(fx.unexpected).toEqual([])
})

test('a sign-in that expires mid-session says so on the sign-in screen', async ({ page }, info) => {
  const fx = await adminFixture(page, info)
  await page.goto('/admin/usage')
  await expect(page.getByRole('table', { name: '사용자별' })).toBeVisible({ timeout: 15_000 })
  fx.state.expired = true
  await page.getByRole('button', { name: '30일' }).click()
  await expect(page.getByText('로그인이 만료되었습니다. 다시 로그인하면 하던 화면으로 돌아갑니다.')).toBeVisible()
  // The address is kept, so signing in again returns here.
  await expect(page).toHaveURL(/\/admin\/usage$/)
})

test('the privacy card lets the person pick which strict-local model takes the turn', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info, { skills: false, agents: false, connectors: false })
  const sent: Record<string, unknown>[] = []
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const body = route.request().postDataJSON() as Record<string, unknown>
    sent.push(body)
    if (body.privacyAction !== 'route_strict_local') {
      return route.fulfill({ status: 409, json: {
        code: 'privacy_decision_required', findings: [{ category: 'email', source: 'current_input', count: 1 }],
        requestedModels: ['fixture/model'],
        safeModels: [{ id: 'strict-local/c', label: '로컬 C' }, { id: 'strict-local/a', label: '로컬 A' }],
        allowedActions: ['route_strict_local', 'mask_external', 'edit', 'cancel'], decisionToken: 'token-1',
        detectorVersion: 'v1', policyVersion: 'v1',
      } })
    }
    return route.fulfill({ contentType: 'text/event-stream', body: [
      { type: 'delta', text: '로컬에서 답했습니다.' }, { type: 'usage', inputTokens: 1, outputTokens: 1, credits: 0 },
      { type: 'done' },
    ].map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') })
  })
  await page.goto('/')
  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill('contact person@example.com')
  await composer.press('Enter')
  const card = page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })
  const pick = card.getByRole('combobox', { name: '전환할 로컬 모델' })
  // The admin's first is preselected; the person picks the second.
  await expect(pick).toHaveValue('strict-local/c')
  await pick.selectOption('strict-local/a')
  await card.getByRole('button', { name: '안전한 로컬 모델로 전환' }).click()
  await expect.poll(() => sent.at(-1)?.privacyModelId).toBe('strict-local/a')
  expect(sent.at(-1)?.privacyAction).toBe('route_strict_local')
  expect(unexpected).toEqual([])
})

test('pending sign-ups are approved together from a selection', async ({ page }, info) => {
  const fx = await adminFixture(page, info)
  await page.goto('/admin/users')
  await expect(page.getByTestId('users-range')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('checkbox', { name: '이 페이지 전체 선택' }).check()
  const bar = page.getByTestId('users-bulk')
  await expect(bar).toContainText('3명 선택')
  await expect(bar).toContainText('승인 대기 2명')
  await bar.getByRole('button', { name: '선택 승인' }).click()
  const dialog = page.getByRole('dialog', { name: '2명을 승인할까요?' })
  await dialog.getByRole('button', { name: '승인' }).click()
  await expect(page.getByText('2명을 승인했습니다.')).toBeVisible()
  // The admin's own active account was skipped.
  expect(fx.decided).toEqual(['approve p1', 'approve p2'])
  await expect(bar).toBeHidden()
  expect(fx.unexpected).toEqual([])
})

test('a table in an answer sorts by its headings and goes back to the written order', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info, { skills: false, agents: false, connectors: false })
  await page.goto(`/s/${chatId}`)
  const table = page.getByRole('table').filter({ hasText: '지원 규모' })
  await expect(table).toBeVisible({ timeout: 15_000 })
  const first = () => table.locator('tbody tr').first().locator('td').first()
  await expect(first()).toHaveText('지원 규모')
  const heading = table.getByRole('columnheader', { name: '항목' })
  await heading.getByRole('button').click()
  await expect(heading).toHaveAttribute('aria-sort', 'ascending')
  await expect(first()).toHaveText('수행 기간')
  await heading.getByRole('button').click()
  await heading.getByRole('button').click()
  await expect(heading).toHaveAttribute('aria-sort', 'none')
  await expect(first()).toHaveText('지원 규모')
  expect(unexpected).toEqual([])
})

test('a report started from a project shows the home screen chips, its default template, and sends the picked file', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info, { skills: false, agents: false, connectors: false })
  const id = 'c0000000000000000000000000000077'
  const project = {
    id: 'p1', name: '사업 계획', description: '', emoji: '📁', instructions: '', files: [], sessionIds: [id],
    skillIds: [], designSystemId: null, renderTemplates: { report: 'tpl-report' }, updatedAt: '2026-10-09T07:00:00.000Z',
  }
  const empty = {
    id, kind: 'report', title: '새 대화', model: 'fixture/model', routingMode: 'manual', projectId: 'p1', agentId: null,
    artifactId: null, pending: null, skillIds: [], renderTemplateId: null, pinned: false, messageCount: 0,
    createdAt: '2026-10-09T07:00:00.000Z', updatedAt: '2026-10-09T07:00:00.000Z', messages: [], preview: null, made: null,
  }
  await page.route('**/api/projects', (route) => route.request().method() === 'GET' ? route.fulfill({ json: [project] }) : route.fallback())
  await page.route('**/api/sessions', (route) => route.request().method() === 'GET' ? route.fulfill({ json: [empty] }) : route.fallback())
  await page.route(`**/api/sessions/${id}`, (route) => route.request().method() === 'GET' ? route.fulfill({ json: empty }) : route.fallback())
  await page.route(`**/api/sessions/${id}/messages`, (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: [] })
    sends.push({ path: `/sessions/${id}/messages`, body: route.request().postDataJSON() })
    return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"type":"done"}\n\n' })
  })
  await page.goto(`/s/${id}`)
  // The new subtitle, the same 형식 and 템플릿 chips as the home screen, the project's template picked.
  await expect(page.getByText('웹 보고서·한글·Word로 씁니다', { exact: false })).toBeVisible({ timeout: 15_000 })
  const format = page.getByRole('button', { name: /^형식/ })
  await expect(format).toContainText('웹 보고서')
  await expect(page.getByTestId('template-chip')).toContainText('비교 보고서')
  await format.click()
  await page.getByRole('menuitemcheckbox', { name: 'Word', exact: true }).click()
  await expect(page.getByRole('button', { name: '빈 문서로 시작' })).toBeVisible()
  await page.getByLabel('프롬프트 입력').fill('3분기 실적 보고서')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => sends.at(-1)?.body.outputFormat).toBe('docx')
  expect(sends.at(-1)?.body.renderTemplateId).toBe('tpl-report')
  expect(unexpected).toEqual([])
})

test('an empty slides session reopened later (an agent\'s start) offers the same format and template chips', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info, { skills: false, connectors: false })
  const id = 'c0000000000000000000000000000078'
  const empty = {
    id, kind: 'slides', title: '새 대화', model: 'fixture/model', routingMode: 'manual', projectId: null,
    agentId: `a${'1'.repeat(32)}`, artifactId: null, pending: null, skillIds: [], renderTemplateId: null, pinned: false,
    messageCount: 0, createdAt: '2026-10-09T07:00:00.000Z', updatedAt: '2026-10-09T07:00:00.000Z', messages: [],
    preview: null, made: null,
  }
  await page.route('**/api/sessions', (route) => route.request().method() === 'GET' ? route.fulfill({ json: [empty] }) : route.fallback())
  await page.route(`**/api/sessions/${id}`, (route) => route.request().method() === 'GET' ? route.fulfill({ json: empty }) : route.fallback())
  await page.route(`**/api/sessions/${id}/messages`, (route) => route.request().method() === 'GET' ? route.fulfill({ json: [] }) : route.fallback())
  await page.goto(`/s/${id}`)
  const format = page.getByRole('button', { name: /^형식/ })
  await expect(format).toContainText('웹 슬라이드', { timeout: 15_000 })
  await expect(page.getByTestId('template-chip')).toContainText('기본값')
  await page.getByTestId('template-chip').click()
  await expect(page.getByRole('dialog', { name: '템플릿 고르기' })).toBeVisible()
  await page.keyboard.press('Escape')
  await format.click()
  await page.getByRole('menuitemcheckbox', { name: 'PowerPoint', exact: true }).click()
  await expect(page.getByRole('button', { name: '빈 문서로 시작' })).toBeVisible()
  expect(unexpected).toEqual([])
})

test('the outline card shows the report\'s file and sends a changed one with the approval', async ({ page }, info) => {
  const { unexpected, sends } = await composerFixture(page, info, { skills: false, agents: false, connectors: false })
  const id = 'c0000000000000000000000000000079'
  const waiting = {
    id, kind: 'report', title: '실적 보고서', model: 'fixture/model', routingMode: 'manual', projectId: null, agentId: null,
    artifactId: null, skillIds: [], renderTemplateId: null, pinned: false, messageCount: 1,
    createdAt: '2026-10-09T07:00:00.000Z', updatedAt: '2026-10-09T07:00:00.000Z', preview: null, made: null,
    messages: [{ id: 'm0000000000000000000000000000079', role: 'user', content: '3분기 실적 보고서', attachments: [],
      createdAt: '2026-10-09T07:00:00.000Z' }],
    pending: { stage: 'outline', request: '3분기 실적 보고서', attachments: [], answers: {}, output: 'hwp',
      plan: { title: '3분기 실적', sections: ['개요', '실적', '전망'] } },
  }
  await page.route('**/api/sessions', (route) => route.request().method() === 'GET' ? route.fulfill({ json: [waiting] }) : route.fallback())
  await page.route(`**/api/sessions/${id}`, (route) => route.request().method() === 'GET' ? route.fulfill({ json: waiting }) : route.fallback())
  await page.route(`**/api/sessions/${id}/messages`, (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: waiting.messages })
    sends.push({ path: `/sessions/${id}/messages`, body: route.request().postDataJSON() })
    return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"type":"done"}\n\n' })
  })
  await page.goto(`/s/${id}`)
  const format = page.getByRole('combobox', { name: '형식' })
  await expect(format).toHaveValue('hwp', { timeout: 15_000 })
  await format.selectOption('docx')
  await page.getByRole('button', { name: '이대로 생성' }).click()
  await expect.poll(() => sends.at(-1)?.body.outputFormat).toBe('docx')
  expect(sends.at(-1)?.body.approve).toBe(true)
  expect(unexpected).toEqual([])
})

test('handing a chat to a report asks which file and carries it to the first turn', async ({ page }, info) => {
  const agentId = `a${'1'.repeat(32)}`
  const { unexpected, sends } = await composerFixture(page, info, { skills: false, connectors: false })
  const chat = 'c0000000000000000000000000000080'
  const reportId = 'r0000000000000000000000000000080'
  const at = '2026-10-09T07:00:00.000Z'
  const row = (id: string, kind: string, messages: unknown[]) => ({
    id, kind, title: '에이전트 대화', model: 'fixture/model', routingMode: 'manual', projectId: null, agentId,
    artifactId: null, pending: null, skillIds: [], renderTemplateId: null, pinned: false, messageCount: messages.length,
    createdAt: at, updatedAt: at, messages, preview: null, made: null,
  })
  const talk = [
    { id: 'm0000000000000000000000000000081', role: 'user', content: '분기 실적을 정리해 보자', attachments: [], createdAt: at },
    { id: 'm0000000000000000000000000000082', role: 'assistant', content: '좋습니다.', attachments: [], model: 'fixture/model', createdAt: at },
  ]
  const handed: Record<string, unknown>[] = []
  await page.route('**/api/sessions', (route) => route.request().method() === 'GET' ? route.fulfill({ json: [row(chat, 'chat', talk)] }) : route.fallback())
  await page.route(`**/api/sessions/${chat}`, (route) => route.request().method() === 'GET' ? route.fulfill({ json: row(chat, 'chat', talk) }) : route.fallback())
  await page.route(`**/api/sessions/${chat}/messages`, (route) => route.request().method() === 'GET' ? route.fulfill({ json: talk }) : route.fallback())
  await page.route(`**/api/sessions/${chat}/handoff`, (route) => {
    handed.push(route.request().postDataJSON())
    return route.fulfill({ status: 201, json: { session: row(reportId, 'report', []), attachments: [], outputFormat: 'hwp' } })
  })
  await page.route(`**/api/sessions/${reportId}/messages`, (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: [] })
    sends.push({ path: `/sessions/${reportId}/messages`, body: route.request().postDataJSON() })
    return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"type":"done"}\n\n' })
  })
  await page.route(`**/api/sessions/${reportId}`, (route) => route.request().method() === 'GET' ? route.fulfill({ json: row(reportId, 'report', []) }) : route.fallback())
  await page.goto(`/s/${chat}`)
  await page.getByRole('button', { name: '보고서 만들기' }).click({ timeout: 15_000 })
  await page.getByRole('menuitem', { name: '한글', exact: true }).click()
  await expect.poll(() => handed.at(-1)).toEqual({ kind: 'report', outputFormat: 'hwp' })
  await expect.poll(() => sends.at(-1)?.body.outputFormat).toBe('hwp')
  expect(unexpected).toEqual([])
})
