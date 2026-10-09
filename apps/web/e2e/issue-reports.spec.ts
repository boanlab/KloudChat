import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { openSidebar } from './helpers'

/**
 * 「문의·신고」 against fixture data: a 👎 answer reported with the answer pinned, the session
 * shared only when ticked (and a strict-local one confirmed again), a screenshot sent along;
 * the account menu's 「문의·신고」 page with the reply badge and its 「새 신고」·「새 문의」, which keep
 * the conversation the user came from; the administrator's queue, status flow, reply, and the
 * read-only session copy with the reported answer highlighted.
 */

test.use({ serviceWorkers: 'block' })

const at = '2026-10-09T03:00:00.000Z'
const sessionId = 'b5555555555555555555555555555555'
const answerId = 'm2'
const shotsDir = process.env.ISSUE_SHOTS ?? ''
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
)

function run(withEvidence: boolean) {
  const runId = 'r1'
  const rec = (name: string, label: string, kind: string, status: string, summary: string, reason = '') => ({
    id: `${runId}:${name}:1`, runId, name, label, kind, status, reason, summary, startedAt: at, finishedAt: at,
    ms: 1200, attempt: 1, rerunnable: false,
    ...(withEvidence && name === 'verify' ? { evidence: { findings: { before: ['ungrounded'], after: ['ungrounded'] } } } : {}),
  })
  return {
    id: runId, pipeline: 'chat', flow: 'answer', status: 'failed', parentId: null, fromStage: null,
    stages: [rec('draft', '초안', 'model', 'done', '답 380자'), rec('verify', '검증', 'check', 'failed', '', 'timeout')],
  }
}

function message(id: string, role: 'user' | 'assistant', content: string, extra: Record<string, unknown> = {}) {
  return {
    id, role, content, steps: null, attachments: null, variants: null, usage: null, model: role === 'assistant' ? 'strict-local/w' : null,
    routing: null, startedFrom: null, rating: null, artifactIds: null, failure: null, createdAt: at, ...extra,
  }
}

const messages = [
  message('m1', 'user', '2025년 매출 표를 정리해 줘'),
  message(answerId, 'assistant', '표의 합계가 1,200원으로 맞지 않습니다.', {
    rating: 'down', routing: { action: 'internal_strict_local', run: run(false) },
  }),
]

const session = {
  id: sessionId, kind: 'chat', title: '매출 표 정리', projectId: null, agentId: null, model: 'strict-local/w',
  routingMode: 'manual', artifactId: null, pending: null, skillIds: [], renderTemplateId: null, pinned: false,
  createdAt: at, updatedAt: at, preview: null, messageCount: 2, made: null, messages,
}

function reportRow(extra: Record<string, unknown> = {}) {
  return {
    id: 'i1', kind: 'bug', status: 'received', body: '표의 합계가 틀립니다', holdReason: '', sessionId, messageId: answerId,
    artifactId: null, sessionShared: true, hasScreenshot: true,
    snapshot: { title: '매출 표 정리', kind: 'chat', messages: 2, artifacts: 0, files: 1, bytes: 4000, truncated: [] },
    unread: false, assignee: null, reporter: { id: 'fixture-user', name: '김신고', email: 'fixture@example.test' },
    replies: 0, createdAt: at, updatedAt: at, adminUpdatedAt: null, ...extra,
  }
}

const snapshot = {
  session: { id: sessionId, title: '매출 표 정리', kind: 'chat', model: 'strict-local/w', agent: '데이터 분석', project: null,
    pipeline: null, artifactId: null, createdAt: at, updatedAt: at },
  messages,
  runs: [{ ...run(true), sessionId, messageId: answerId, artifactId: null, artifactVersion: null, createdAt: at, updatedAt: at }],
  artifacts: [],
  files: [{ id: 'f1', name: '매출.csv', mime: 'text/csv', size: 2048, createdAt: at, copied: true }],
  pinned: { messageId: answerId, artifactId: null },
  truncated: [{ code: 'evidence' }],
}

async function fixture(page: Page, testInfo: TestInfo, { admin = false } = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const problems: string[] = []
  const created: Record<string, unknown>[] = []
  const calls: string[] = []
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  let mine: ReturnType<typeof reportRow>[] = []
  let adminRow = reportRow()
  let thread: Record<string, unknown>[] = []

  await page.context().routeWebSocket(/^(?!wss?:\/\/[^/]+\/\?token=).*$/, (socket) => { unexpected.push(`WebSocket ${socket.url()}`); socket.close() })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method !== 'GET' && path !== '/auth/refresh') calls.push(`${method} ${path}`)
    const json = () => (request.postDataJSON() ?? {}) as Record<string, unknown>
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: '김신고', email: 'fixture@example.test', role: admin ? 'admin' : 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
        createdAt: at, preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report', 'slides'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'strict-local/w', label: 'Local',
      name: 'Local', vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report', 'slides'], modality: 'chat',
      dataBoundary: 'self_hosted', creditCost: 0, inputCreditCost: 0, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'strict-local/w', litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [{ ...session, messages: null }] })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'GET' && path === `/sessions/${sessionId}/runs`) return route.fulfill({ json: [] })
    // people
    if (method === 'GET' && path === `/issues/session-info/${sessionId}`) return route.fulfill({ json: { id: sessionId,
      title: '매출 표 정리', kind: 'chat', messageCount: 2, artifactCount: 0, fileCount: 1, runCount: 1, strictLocal: true,
      models: ['strict-local/w'], agent: null, pipeline: null } })
    if (method === 'POST' && path === '/files') return route.fulfill({ status: 201, json: { id: 'shot1', name: 'shot.png',
      size: PNG.length, mime: 'image/png', tokens: 0, createdAt: at } })
    if (method === 'POST' && path === '/issues') {
      const body = json()
      created.push(body)
      const row = reportRow({ id: `i${created.length}`, kind: body.kind, body: body.body, sessionShared: Boolean(body.shareSession),
        hasScreenshot: Boolean(body.screenshotFileId), reporter: null, snapshot: null })
      mine = [row, ...mine]
      return route.fulfill({ status: 201, json: row })
    }
    if (method === 'GET' && path === '/issues') return route.fulfill({ json: mine })
    if (method === 'GET' && path === '/issues/unread') return route.fulfill({ json: { count: mine.filter((r) => r.unread).length } })
    if (method === 'GET' && /^\/issues\/[^/]+$/.test(path)) {
      const row = mine.find((r) => path === `/issues/${r.id}`)
      return route.fulfill({ json: { ...row, thread } })
    }
    if (method === 'POST' && /^\/issues\/[^/]+\/read$/.test(path)) {
      mine = mine.map((r) => ({ ...r, unread: false }))
      return route.fulfill({ json: { ok: true } })
    }
    if (method === 'GET' && /\/screenshot$/.test(path)) return route.fulfill({ body: PNG, contentType: 'image/png' })
    // administrators
    if (method === 'GET' && path === '/admin/issues') {
      const status = url.searchParams.get('status')
      const items = [adminRow, reportRow({ id: 'i2', kind: 'feature', body: '다크 모드에서 표 테두리를 진하게', sessionShared: false,
        hasScreenshot: false, snapshot: null, reporter: { id: 'u2', name: '박요청', email: 'park@example.test' } })]
        .filter((r) => !status || r.status === status)
      return route.fulfill({ json: { items, total: items.length, page: 1, size: 30,
        counts: { received: 2, checking: 0, resolved: 0, on_hold: 0 }, admins: [{ id: 'fixture-user', name: '김신고' }] } })
    }
    const detail = () => ({ ...adminRow, thread, context: {
      client: { userAgent: 'Mozilla/5.0 (fixture)', appBuild: 'index-abc123.js', path: `/s/${sessionId}`, viewport: '1440x900', lang: 'ko' },
      apiVersion: '0.1.0',
      session: { id: sessionId, kind: 'chat', model: 'strict-local/w', routingMode: 'manual', agent: '데이터 분석', pipeline: null,
        renderTemplateId: null, messageCount: 2, strictLocal: true },
      models: ['strict-local/w'], failures: [], runs: [{ ...run(false), sessionId, messageId: answerId, artifactId: null,
        artifactVersion: null, createdAt: at, updatedAt: at }], artifacts: [], pinned: { messageId: answerId, artifactId: null } },
      admins: [{ id: 'fixture-user', name: '김신고' }],
      transitions: adminRow.status === 'received' ? ['checking', 'on_hold', 'resolved'] : adminRow.status === 'on_hold'
        ? ['checking', 'resolved'] : ['on_hold', 'resolved'] })
    if (method === 'GET' && path === '/admin/issues/i1') return route.fulfill({ json: detail() })
    if (method === 'PATCH' && path === '/admin/issues/i1') {
      const body = json() as { status?: string; holdReason?: string; assigneeId?: string }
      if (body.status === 'on_hold' && !body.holdReason) return route.fulfill({ status: 422, json: { detail: 'hold_reason_required' } })
      if (body.assigneeId !== undefined) adminRow = { ...adminRow, assignee: body.assigneeId ? { id: body.assigneeId, name: '김신고' } : null } as typeof adminRow
      if (body.status) {
        adminRow = { ...adminRow, status: body.status, holdReason: body.holdReason ?? '' }
        thread = [...thread, { id: `t${thread.length}`, authorRole: 'admin', authorName: '김신고', body: body.holdReason ?? '', status: body.status, createdAt: at }]
      }
      return route.fulfill({ json: detail() })
    }
    if (method === 'POST' && path === '/admin/issues/i1/replies') {
      const reply = { id: `t${thread.length}`, authorRole: 'admin', authorName: '김신고', body: json().body, status: null, createdAt: at }
      thread = [...thread, reply]
      return route.fulfill({ status: 201, json: reply })
    }
    if (method === 'GET' && path === '/admin/issues/i1/snapshot') return route.fulfill({ json: snapshot })
    if (method === 'GET' && path.startsWith('/admin/')) return route.fulfill({ json: path === '/admin/users' ? [] : {} })
    if (method === 'GET' && (path.startsWith('/sessions/') && (path.endsWith('/messages') || path.endsWith('/jobs')))) return route.fulfill({ json: [] })
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    if (method === 'GET' && ['/projects', '/skills', '/memory', '/tools', '/connectors', '/connectors/catalog', '/agents',
      '/jobs', '/designs', '/shares', '/design-templates', '/skills/store', '/artifacts', '/files'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  return {
    unexpected, problems, created, calls,
    giveReply: () => {
      mine = mine.map((r) => ({ ...r, unread: true, status: 'checking', replies: 1, adminUpdatedAt: at }))
      thread = [{ id: 't0', authorRole: 'admin', authorName: '관리자', body: '재현했습니다. 다음 배포에서 고칩니다.', status: null, createdAt: at }]
    },
  }
}

function shooter(page: Page, testInfo: TestInfo) {
  const dir = shotsDir ? `${shotsDir}/${testInfo.project.name}` : testInfo.outputPath('shots')
  let n = 0
  return async (name: string, full = false) => {
    n += 1
    // Let menus, drawers and dialogs finish their transitions.
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${dir}/${testInfo.title.split(':')[0]}-${String(n).padStart(2, '0')}-${name}.png`, fullPage: full })
  }
}

test('reporter: report a 👎 answer with consent, a screenshot and the extra strict-local check', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)
  await page.goto(`/s/${sessionId}`)
  const report = page.getByRole('button', { name: '신고하기' })
  await expect(report).toBeVisible({ timeout: 20_000 })
  await report.click()

  const dialog = page.getByRole('dialog', { name: '문의·신고' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('표의 합계가 1,200원으로')).toBeVisible()
  const share = dialog.getByRole('checkbox', { name: '이 세션을 관리자에게 공유합니다' })
  await expect(share).not.toBeChecked()
  await dialog.getByRole('textbox').first().fill('합계가 틀리게 나옵니다. 1,000원이어야 합니다.')
  await dialog.getByLabel('스크린샷 고르기').setInputFiles({ name: 'shot.png', mimeType: 'image/png', buffer: PNG })
  await expect(dialog.getByRole('img', { name: '스크린샷 미리보기' })).toBeVisible()
  await share.check()
  // A strict-local conversation asks once more; sending waits for it.
  await expect(dialog.getByRole('alert')).toContainText('strict-local')
  const send = dialog.getByRole('button', { name: '보내기' })
  await expect(send).toBeDisabled()
  await shot('form-strict-confirm')
  await dialog.getByRole('checkbox', { name: '내부 자료가 포함된 것을 알고 공유합니다' }).check()
  await expect(send).toBeEnabled()
  await send.click()

  await expect(dialog.getByText('접수되었습니다')).toBeVisible()
  const body = state.created[0] as Record<string, unknown>
  expect(body).toMatchObject({ kind: 'bug', sessionId, shareSession: true, confirmInternal: true, messageId: answerId, screenshotFileId: 'shot1' })
  expect(Object.keys(body.client as object)).toEqual(expect.arrayContaining(['userAgent', 'appBuild', 'path']))
  expect(state.calls).toEqual(['POST /files', 'POST /issues'])
  await shot('sent')

  // An administrator answers; the badge and 「문의·신고」 show it.
  state.giveReply()
  await dialog.getByRole('button', { name: '문의·신고 보기' }).click()
  await expect(page).toHaveURL(/\/issues$/)
  const item = page.getByTestId('my-issue').first()
  await expect(item.getByText('새 답변')).toBeVisible()
  await item.getByRole('button').first().click()
  await expect(item.getByText('재현했습니다. 다음 배포에서 고칩니다.')).toBeVisible()
  await expect.poll(() => state.calls.includes('POST /issues/i1/read')).toBe(true)
  await shot('my-reports', true)
  expect(state.unexpected).toEqual([])
  expect(state.problems).toEqual([])
})

test('reporter: the account menu opens 「문의·신고」; new report and inquiry keep the session', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)
  await page.goto(`/s/${sessionId}`)
  await openSidebar(page)
  await page.getByRole('button', { name: /^계정 메뉴/ }).first().click()
  // One entry: no separate 「내 신고」.
  await expect(page.getByRole('menuitem', { name: '내 신고' })).toHaveCount(0)
  await shot('menu')
  await page.getByRole('menuitem', { name: '문의·신고' }).click()
  await expect(page).toHaveURL(/\/issues$/)
  await expect(page.getByRole('heading', { name: '문의·신고' })).toBeVisible()
  await expect(page.getByText('보낸 문의·신고가 없습니다')).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await shot('page-empty', true)

  // 「새 문의」 starts as an inquiry, the conversation the user came from on offer, not ticked.
  await page.getByRole('button', { name: '새 문의' }).click()
  let dialog = page.getByRole('dialog', { name: '문의·신고' })
  await expect(dialog.getByRole('radio', { name: '문의' })).toHaveAttribute('aria-checked', 'true')
  await expect(dialog.getByText('세션 첨부')).toBeVisible()
  await dialog.getByRole('textbox').first().fill('표를 CSV로 받을 수 있나요?')
  await dialog.getByRole('button', { name: '보내기' }).click()
  await expect(dialog.getByText('접수되었습니다')).toBeVisible()
  await dialog.getByRole('button', { name: '닫기', exact: true }).last().click()
  expect(state.created[0]).toMatchObject({ kind: 'other', sessionId, shareSession: false, confirmInternal: false })
  expect(state.created[0].messageId).toBeUndefined()
  await expect(page.getByTestId('my-issue')).toHaveCount(1)
  await expect(page.getByTestId('my-issue').getByText('문의', { exact: true })).toBeVisible()

  // 「새 신고」 starts as a bug report; the chooser still offers the rest.
  await page.getByRole('button', { name: '새 신고' }).click()
  dialog = page.getByRole('dialog', { name: '문의·신고' })
  await expect(dialog.getByRole('radio', { name: '오류 신고' })).toHaveAttribute('aria-checked', 'true')
  await shot('new-report')
  await dialog.getByRole('radio', { name: '기능 요청' }).click()
  await dialog.getByRole('textbox').first().fill('표를 CSV로 바로 받고 싶습니다.')
  await dialog.getByLabel('이 세션을 관리자에게 공유합니다').check()
  await dialog.getByRole('checkbox', { name: '내부 자료가 포함된 것을 알고 공유합니다' }).check()
  await dialog.getByRole('button', { name: '보내기' }).click()
  await expect(dialog.getByText('접수되었습니다')).toBeVisible()
  await dialog.getByRole('button', { name: '닫기', exact: true }).last().click()
  expect(state.created[1]).toMatchObject({ kind: 'feature', sessionId, shareSession: true })
  await expect(page.getByTestId('my-issue')).toHaveCount(2)
  await shot('page-list', true)

  // Opened from outside a conversation there is none to attach. (A reload of the page keeps
  // the router state, so leave it first.)
  await page.goto('/')
  await page.goto('/issues')
  await page.getByRole('button', { name: '새 신고' }).click()
  dialog = page.getByRole('dialog', { name: '문의·신고' })
  await expect(dialog.getByRole('radio', { name: '오류 신고' })).toHaveAttribute('aria-checked', 'true')
  await expect(dialog.getByRole('textbox').first()).toBeVisible()
  await expect(dialog.getByText('세션 첨부')).toHaveCount(0)
  expect(state.unexpected).toEqual([])
  expect(state.problems).toEqual([])
})

test('admin: queue, status flow with a hold reason, reply, read-only session copy', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo, { admin: true })
  const shot = shooter(page, testInfo)
  await page.goto('/admin/issues')
  await expect(page.getByTestId('admin-issue')).toHaveCount(2, { timeout: 20_000 })
  await shot('queue')
  await page.getByRole('combobox', { name: '상태' }).selectOption('received')
  await page.getByTestId('admin-issue').first().click()
  await expect(page).toHaveURL(/\/admin\/issues\/i1$/)
  await expect(page.getByTestId('issue-body')).toHaveText('표의 합계가 틀립니다')
  await expect(page.getByRole('img', { name: '보낸 스크린샷' })).toBeVisible()
  await expect(page.getByText('index-abc123.js · API 0.1.0')).toBeVisible()

  // On hold needs a reason; the change and the reply go to the reporter's thread.
  await page.getByRole('combobox', { name: '상태 바꾸기' }).selectOption('on_hold')
  await expect(page.getByRole('button', { name: '상태 저장' })).toBeDisabled()
  await page.getByPlaceholder('신고한 사람에게 보입니다').fill('다음 배포에서 고칩니다')
  await page.getByRole('button', { name: '상태 저장' }).click()
  await expect(page.getByText('· 다음 배포에서 고칩니다')).toBeVisible()
  await page.getByRole('textbox', { name: '답변 쓰기' }).fill('재현했습니다.')
  await page.getByRole('button', { name: '답변 보내기' }).click()
  await expect(page.getByText('재현했습니다.', { exact: true })).toBeVisible()

  // The copy opens on request (audited server-side), read-only, the reported answer marked.
  expect(state.calls).not.toContain('GET /admin/issues/i1/snapshot')
  await page.getByRole('button', { name: '세션 사본 열기' }).click()
  const copy = page.getByTestId('issue-snapshot')
  await expect(copy).toBeVisible()
  const pinned = copy.locator('[data-pinned]')
  await expect(pinned).toHaveCount(1)
  await expect(pinned).toContainText('표의 합계가 1,200원으로')
  await expect(pinned.getByText('신고한 답변')).toBeVisible()
  // The route reads in words, not as its code.
  await expect(pinned.getByText('내부 자료 strict-local 처리')).toBeVisible()
  await expect(copy.getByText('internal_strict_local')).toHaveCount(0)
  await expect(pinned.getByTestId('stage-list')).toBeVisible()
  await expect(copy.getByText('용량 한도로 단계 근거를 뺐습니다')).toBeVisible()
  // Read-only: no rerun, rating or edit controls.
  await expect(copy.getByRole('button', { name: /단계 메뉴|좋아요|싫어요|편집/ })).toHaveCount(0)
  await shot('detail-snapshot', true)
  expect(state.calls).toEqual(['PATCH /admin/issues/i1', 'POST /admin/issues/i1/replies'])
  expect(state.unexpected).toEqual([])
  expect(state.problems).toEqual([])
})
