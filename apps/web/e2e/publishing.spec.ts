import { expect, test, type Page, type Route } from '@playwright/test'
import { agentRow, hubFixture, myAgents } from './customize-fixture'
import { viewport } from './project'

/**
 * 공개 챗봇 against fixture data: the publish dialog with its audience (members at once; a link
 * or the public listing asked of an administrator), the bot's page for a member and for a
 * visitor without an account (notice, streaming, 👍👎, 만든 사람에게 보내기, paused, blocked),
 * the owner's dashboard (audience, review, blocking), the members' and the public list, the
 * administrator's review, and the same screens in English. Also the channels: asking for one,
 * keys and the API tab, the page framed on another site and the loader that frames it, and an
 * administrator reading conversations with a reason. Screenshots go to PUBLISHING_SHOTS when it
 * is set.
 */

const shots = process.env.PUBLISHING_SHOTS ?? ''
const at = '2026-10-10T07:00:00.000Z'
const AGENT = myAgents[0].id as string
const PUB = 'p1111111111111111111111111111111'
const SLUG = 'haksa-1a2b3c'
const SID = 's1111111111111111111111111111111'

const limits = { perVisitorPerMinute: 10, perVisitorPerDay: 200, perBotPerMinute: 60, perIpPerMinute: 20, perIpPerDay: 400,
  turnsPerDay: 2000, tokensPerDay: 4000000, maxInputChars: 8000, turnsPerConversation: 100, concurrency: 4,
  perKeyPerMinute: 30, perKeyPerDay: 2000 }
const SITE = 'https://www.example.ac.kr'

function publication(extra: Record<string, unknown> = {}) {
  return {
    id: PUB, slug: SLUG, title: '학사 안내', description: '학칙과 학사 일정을 안내합니다.', agentId: AGENT, agentName: '논문 리뷰어',
    ownerName: 'Fixture', visibility: 'internal', status: 'approved', pauseReason: '', activeVersionId: 'v2',
    branding: { greeting: '학칙을 찾아 근거 조항과 함께 답합니다.', suggestedPrompts: ['휴학은 언제까지 신청하나요?'], placeholder: '' },
    showProcess: 'summary', showSources: true, allowCopy: false, retentionDays: 30, conversationView: 'off', consentVersion: 1,
    limits, defaults: limits, createdAt: at, updatedAt: at, publishedAt: at,
    requestedVisibility: '', reviewState: '', reviewNote: '', reviewedAt: null, pendingVersionId: null, needsLook: false,
    channels: {}, requestedChannels: null,
    versions: [
      { id: 'v2', number: 2, model: 'strict-local/qwen', note: '2026학년도 학칙', active: true, createdAt: at,
        summary: { knowledge: [{ name: '학칙.pdf', chars: 12000 }] } },
      { id: 'v1', number: 1, model: 'strict-local/qwen', note: '', active: false, createdAt: at, summary: { knowledge: [] } },
    ],
    ...extra,
  }
}

const preflight = (extra: Record<string, unknown> = {}) => ({
  ok: true, problems: [], model: { id: 'strict-local/qwen', label: '내부 Qwen' }, tools: ['calculate', 'search_knowledge'],
  checks: ['answer-hold', 'contact-note', 'pii-mask'],
  disabled: ['memory', 'personal', 'files', 'connectors', 'web', 'documents'],
  knowledge: [{ name: '학칙.pdf', chars: 12000, findings: [{ category: 'phone', count: 1 }] }],
  creditsRemaining: 900, existingId: null, defaults: limits, ...extra,
})

function page_(extra: Record<string, unknown> = {}) {
  return {
    slug: SLUG, title: '학사 안내', description: '학칙과 학사 일정을 안내합니다.', ownerName: '학사지원팀 김담당', mine: false,
    signedIn: true, visibility: 'internal', blocked: false, status: 'approved', pauseReason: '', branding: publication().branding, showSources: true, showProcess: 'summary',
    conversationView: 'off', retentionDays: 30, consentVersion: 1, consented: false, color: '#0f766e', maxInputChars: 8000,
    ...extra,
  }
}

const ANSWER = '휴학은 학기 개시일 전까지 신청합니다 [1].\n\n### 출처\n- [1] 학칙.pdf · 제12조(휴학) · 3쪽 · 시행 2025. 3. 1.'

/** The bot's routes on top of the hub fixture; every write is recorded. */
async function botRoutes(
  page: Page,
  options: { paused?: boolean; english?: boolean; anonymous?: boolean; blocked?: boolean; pub?: Record<string, unknown> } = {},
) {
  const writes: { method: string; path: string; body: unknown }[] = []
  /** Every bot call: whether it was marked as framed, and whether it carried an account. */
  const botCalls: { path: string; embed: string | undefined; auth: string | undefined }[] = []
  let keys: { id: string; name: string; preview: string; createdAt: string; lastUsedAt: string | null }[] = []
  let consented = false
  let shared: string | null = null
  let rating: string | null = null
  let asked = false
  let blocked = false
  let copied = false
  let pub = publication(options.pub)
  const english = options.english
  const bot = () => page_({
    consented, status: options.paused ? 'paused' : 'approved', blocked: options.blocked ?? false,
    ...(options.anonymous ? { signedIn: false, ownerName: '', visibility: 'link' } : {}),
    ...(english ? { title: 'Campus Help', description: 'Answers from the campus rules.', ownerName: 'Jane',
      branding: { greeting: 'Ask about the rules.', suggestedPrompts: ['When is the leave deadline?'], placeholder: '' } } : {}),
  })
  const messages = () => asked ? [
    { id: 'm1', role: 'user', content: '휴학은 언제까지 신청하나요?', rating: null, createdAt: at, steps: null },
    { id: 'm2', role: 'assistant', content: ANSWER, rating, createdAt: at, model: 'strict-local/qwen',
      steps: [{ id: 'h1_0', label: '자료 찾기', status: 'done', category: 'tool' }] },
  ] : []
  const conversation = () => ({ id: SID, title: asked ? '휴학 신청 기한' : '', sharedAt: shared, updatedAt: at, messages: messages() })
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    let body: unknown = null
    try {
      body = request.postDataJSON()
    } catch {
      body = null
    }
    if (method !== 'GET') writes.push({ method, path, body })
    if (path.startsWith('/b/')) {
      const headers = request.headers()
      botCalls.push({ path, embed: headers['x-kloudchat-embed'], auth: headers.authorization })
    }
    if (method === 'GET' && path === '/bots') {
      return json([
        { slug: SLUG, title: english ? 'Campus Help' : '학사 안내', description: english ? 'Answers from the campus rules.' : '학칙과 학사 일정을 안내합니다.',
          ownerName: english ? 'Jane' : '학사지원팀 김담당', mine: false, id: null, status: 'approved', visibility: 'internal', color: '#0f766e', publishedAt: at,
          sharedAgentId: null, copied, copyable: true },
        // Locked by its publisher: used on its page only.
        { slug: 'rules-4q', title: '규정 도우미', description: '', ownerName: '이동료', mine: false, id: null, status: 'approved',
          visibility: 'internal', color: '#5b53e8', publishedAt: at, sharedAgentId: null, copied: false, copyable: false },
        // Its agent is also shared with everyone: listed once, as that agent.
        { slug: 'budget-7k', title: '예산 검토', description: '', ownerName: '이동료', mine: false, id: null, status: 'approved',
          visibility: 'internal', color: '#5b53e8', publishedAt: at, sharedAgentId: 'a5555555555555555555555555555555', copied: false },
        { slug: 'mine-9z', title: english ? 'My Help Desk' : '내 안내 챗봇', description: '', ownerName: 'Fixture', mine: true, id: PUB,
          status: 'paused', visibility: 'link', needsLook: true, color: '#5b53e8', publishedAt: at },
      ])
    }
    if (path === `/bots/${SLUG}/copy` && method === 'POST') {
      copied = true
      return json(agentRow('a6666666666666666666666666666666', '학사 안내', { originId: 'a0000000000000000000000000000001' }), 201)
    }
    if (path === `/b/${SLUG}` && method === 'GET') return json(bot())
    if (path === `/b/${SLUG}/consent` && method === 'POST') {
      consented = true
      return route.fulfill({ status: 204 })
    }
    if (path === `/b/${SLUG}/conversations` && method === 'GET') return json(asked ? [{ id: SID, title: '휴학 신청 기한', updatedAt: at, sharedAt: shared }] : [])
    if (path === `/b/${SLUG}/conversations` && method === 'POST') return json({ id: SID, title: '', updatedAt: at, sharedAt: null }, 201)
    if (path === `/b/${SLUG}/conversations/${SID}/messages` && method === 'POST') {
      if (options.paused) return json({ detail: 'bot_paused' }, 409)
      asked = true
      const events = [
        { type: 'model', models: ['strict-local/qwen'] },
        { type: 'step', id: 'h1_0', label: '자료 찾기', status: 'running', category: 'tool' },
        { type: 'step', id: 'h1_0', label: '자료 찾기', status: 'done', category: 'tool' },
        { type: 'delta', text: '휴학은 학기 개시일 전까지 신청합니다 [1].' },
        { type: 'delta', text: '\n\n### 출처\n- [1] 학칙.pdf · 제12조(휴학) · 3쪽 · 시행 2025. 3. 1.' },
        { type: 'title', title: '휴학 신청 기한' },
        { type: 'usage', inputTokens: 900, outputTokens: 60 },
        { type: 'done', messageId: 'm2' },
      ]
      return route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' },
        body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') })
    }
    if (path === `/b/${SLUG}/conversations/${SID}` && method === 'GET') return json(conversation())
    if (path === `/b/${SLUG}/messages/m2/rating` && method === 'PATCH') {
      rating = (body as { rating: string | null }).rating
      return json({ id: 'm2', rating })
    }
    if (path === `/b/${SLUG}/conversations/${SID}/share` && method === 'POST') {
      shared = at
      return json({ id: SID, sharedAt: at })
    }
    if (path === `/b/${SLUG}/conversations/${SID}/share` && method === 'DELETE') {
      shared = null
      return json({ id: SID, sharedAt: null })
    }
    if (path === '/admin/publications' && method === 'GET') {
      return json([{ ...publication({ reviewState: 'pending', requestedVisibility: 'link', limits: { ...limits, creditsPerDay: 3000 } }),
        ownerEmail: 'owner@example.test', conversations: 4 }])
    }
    if (path === `/admin/publications/${PUB}/review` && method === 'GET') {
      return json({
        publication: publication({ reviewState: 'pending', requestedVisibility: 'link', limits: { ...limits, creditsPerDay: 3000 } }),
        owner: { name: '학사지원팀 김담당', email: 'owner@example.test' },
        version: { ...publication().versions[0], pending: false, approvedAt: null },
        agent: { name: '학사 안내', description: '', instructions: '학칙에서 찾아 근거 조항과 함께 답한다.', contact: '학사지원팀 02-760-1234',
          guide: '', starters: [], tools: ['calculate', 'search_knowledge'], checks: ['answer-hold'], model: 'strict-local/qwen' },
        request: 'visibility',
        changes: [{ field: 'instructions', before: '학칙에서 찾아 답한다.', after: '학칙에서 찾아 근거 조항과 함께 답한다.' }],
        knowledge: [{ name: '학칙.pdf', chars: 12000, findings: [{ category: 'phone', count: 1 }], preview: '제12조(휴학) 문의 [전화번호]' }],
        history: [{ action: 'publication.requested', at, actorId: 'owner', metadata: { visibility: 'link' } }],
      })
    }
    if (path === `/admin/publications/${PUB}/review` && method === 'POST') return json(publication({ reviewState: 'rejected' }))
    if (path === '/public/bots' && method === 'GET') {
      return json([{ slug: SLUG, title: english ? 'Campus Help' : '학사 안내', description: english ? 'Answers from the campus rules.' : '학칙과 학사 일정을 안내합니다.',
        color: '#0f766e', publishedAt: at }])
    }
    if (path === '/publications/preflight' && method === 'POST') return json(preflight())
    if (path === '/publications' && method === 'POST') {
      const sent = body as { visibility?: string }
      const asking = sent.visibility && sent.visibility !== 'internal'
      return json(publication({ versions: [publication().versions[1]],
        ...(asking ? { reviewState: 'pending', requestedVisibility: sent.visibility } : {}) }), 201)
    }
    if (path === `/publications/${PUB}` && method === 'GET') {
      return json(english ? publication({ title: 'Campus Help', agentName: 'Reviewer', versions: publication().versions.map((v) => ({ ...v, note: '' })) }) : pub)
    }
    if (path === `/publications/${PUB}/scope` && method === 'POST') {
      const sent = body as { visibility: string; creditsPerDay?: number }
      pub = publication({ reviewState: 'pending', requestedVisibility: sent.visibility, updatedAt: '2026-10-10T07:01:00.000Z',
        limits: { ...limits, creditsPerDay: sent.creditsPerDay } })
      return json(pub)
    }
    if (path === `/publications/${PUB}/review` && method === 'DELETE') {
      pub = publication({ updatedAt: '2026-10-10T07:02:00.000Z' })
      return json(pub)
    }
    if (path === `/publications/${PUB}/conversations/${SID}/block` && method === 'POST') {
      blocked = true
      return json({ id: 'vis1', label: 'VIS1AB', member: false, blocked: true, blockedAt: at })
    }
    if (path === `/publications/${PUB}/visitors` && method === 'GET') {
      return json(blocked ? [{ id: 'vis1', label: 'VIS1AB', member: false, blocked: true, blockedAt: at }] : [])
    }
    if (path === `/publications/${PUB}/visitors/vis1/block` && method === 'DELETE') {
      blocked = false
      return json({ id: 'vis1', label: 'VIS1AB', member: false, blocked: false, blockedAt: null })
    }
    if (path === `/publications/${PUB}` && method === 'PATCH') return json(publication({ ...(body as object), consentVersion: 2 }))
    if (path === `/publications/${PUB}/stats`) {
      return json({ days: 30, daily: [{ date: '2026-10-10', turns: 12, answers: 11, conversations: 5, visitors: 4, tokens: 9000, unanswered: 2, up: 3, down: 1, shared: 1, failed: 0 }],
        totals: { turns: 12, answers: 11, conversations: 5, visitors: 4, tokens: 9000, unanswered: 2, up: 3, down: 1, shared: 1, failed: 0, visitorsAll: 7 },
        topics: [{ word: english ? 'leave' : '휴학', count: 6 }, { word: english ? 'tuition' : '등록금', count: 3 }],
        unanswered: [{ text: english ? 'Parking fee? call [전화번호]' : '주차 요금은 얼마예요? 제 번호 [전화번호]', at }] })
    }
    if (path === `/publications/${PUB}/conversations` && method === 'GET') {
      return json([{ id: SID, title: english ? 'Leave deadline' : '휴학 신청 기한', sharedAt: at, viewable: false, updatedAt: at, turns: 1 }])
    }
    if (path === `/publications/${PUB}/conversations/${SID}`) {
      return json({ id: SID, title: '휴학 신청 기한', sharedAt: at, updatedAt: at, turns: 1,
        visitor: { id: 'vis1', label: 'VIS1AB', member: false, blocked, blockedAt: blocked ? at : null }, messages: [
        { id: 'm1', role: 'user', content: english ? 'Deadline? I am at [전화번호]' : '휴학 기한이요? 제 번호는 [전화번호]', rating: null, createdAt: at },
        { id: 'm2', role: 'assistant', content: english ? 'Before term starts [1].' : ANSWER, rating: 'down', createdAt: at },
      ] })
    }
    if (path === `/publications/${PUB}/admin-reads` && method === 'GET') {
      return json([{ at, kind: 'read', reason: english ? 'Abuse report check' : '신고 확인' }])
    }
    if (path === `/publications/${PUB}/channels` && method === 'POST') {
      const sent = body as { embed?: { on: boolean; origins: string[] }; api?: boolean; mcp?: boolean }
      const wanted: Record<string, unknown> = {}
      if (sent.embed?.on) wanted.embed = { origins: sent.embed.origins }
      if (sent.api) wanted.api = {}
      if (sent.mcp) wanted.mcp = {}
      pub = { ...pub, requestedChannels: wanted, reviewState: 'pending', updatedAt: '2026-10-10T07:03:00.000Z' }
      return json(pub)
    }
    if (path === `/publications/${PUB}/keys` && method === 'GET') return json(keys)
    if (path === `/publications/${PUB}/keys` && method === 'POST') {
      const made = { id: 'k1', name: (body as { name: string }).name, preview: 'kcb_AbCd…wxyz', createdAt: at, lastUsedAt: null }
      keys = [made, ...keys]
      return json({ ...made, key: 'kcb_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdwxyz' }, 201)
    }
    if (path === `/publications/${PUB}/keys/k1` && method === 'DELETE') {
      keys = keys.filter((k) => k.id !== 'k1')
      return route.fulfill({ status: 204 })
    }
    if (path === `/admin/publications/${PUB}/conversations` && method === 'GET') {
      return json([{ id: SID, title: '휴학 신청 기한', sharedAt: null, viewable: false, updatedAt: at, turns: 1,
        visitor: { id: 'vk', label: 'VK1234', member: false, blocked: false, blockedAt: null, byKey: true } }])
    }
    if (path === `/admin/publications/${PUB}/conversations/${SID}` && method === 'GET') {
      return json({ id: SID, title: '휴학 신청 기한', sharedAt: null, updatedAt: at, turns: 1, messages: [
        { id: 'm1', role: 'user', content: '휴학 기한이요? 제 번호는 [전화번호]', rating: null, createdAt: at },
        { id: 'm2', role: 'assistant', content: ANSWER, rating: null, createdAt: at },
      ] })
    }
    if (path === `/publications/${PUB}/versions/v1/activate` && method === 'POST') {
      return json(publication({ activeVersionId: 'v1', versions: publication().versions.map((v) => ({ ...v, active: v.id === 'v1' })) }))
    }
    return route.fallback()
  })
  return { writes, botCalls }
}

async function settle(page: Page) {
  await page.waitForLoadState('networkidle').catch(() => undefined)
  await page.waitForTimeout(300)
}

async function shot(page: Page, name: string, project: string) {
  if (!shots) return
  await settle(page)
  await page.screenshot({ path: `${shots}/${project}-${name}.png`, fullPage: false })
}

test.use({ serviceWorkers: 'block' })

test('the owner publishes a chat agent to members and gets its address', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  await page.goto(`/customize/agents/${AGENT}`)
  await expect(page.getByTestId('item-detail-header')).toBeVisible({ timeout: 15_000 })
  // The detail's main action on one's own chat agent, not hidden under 더 보기.
  await page.getByRole('button', { name: '챗봇으로 공개' }).filter({ visible: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '챗봇으로 공개' })
  await expect(dialog.getByTestId('publish-model')).toContainText('내부 Qwen')
  // Members by default, at once; nothing asks for a credit cap.
  await expect(dialog.getByRole('radio', { name: /구성원 공개/ })).toHaveAttribute('aria-checked', 'true')
  await expect(dialog.getByTestId('scope-credits')).toHaveCount(0)
  await expect(dialog.getByTestId('publish-disabled')).toContainText('내 메모리')
  await expect(dialog.getByTestId('publish-disabled')).toContainText('웹 검색')
  await expect(dialog.getByTestId('publish-finding')).toContainText('휴대전화 번호 1')
  await shot(page, 'publish-dialog', viewport(testInfo))
  await dialog.getByRole('button', { name: '공개하기' }).click()
  await expect(dialog.getByTestId('publish-done')).toBeVisible()
  await expect(dialog.getByTestId('bot-url')).toContainText(`/b/${SLUG}`)
  await shot(page, 'publish-done', viewport(testInfo))
  const sent = writes.find((w) => w.method === 'POST' && w.path === '/publications')?.body as Record<string, unknown>
  expect(sent).toMatchObject({ agentId: AGENT, visibility: 'internal', showProcess: 'summary', conversationView: 'off', allowCopy: false })
  expect(state.unexpected).toEqual([])
})

test('opening a bot by link asks an administrator and needs a daily credit cap', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  await page.goto(`/customize/agents/${AGENT}`)
  await expect(page.getByTestId('item-detail-header')).toBeVisible({ timeout: 15_000 })
  // The detail's main action on one's own chat agent, not hidden under 더 보기.
  await page.getByRole('button', { name: '챗봇으로 공개' }).filter({ visible: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '챗봇으로 공개' })
  await dialog.getByRole('radio', { name: /링크 공개/ }).click()
  await expect(dialog.getByTestId('scope-picker')).toContainText('관리자가 승인하면 열립니다')
  await expect(dialog.getByRole('button', { name: '공개하기' })).toBeDisabled()
  // Out of range says why, next to the box, instead of only greying the button out.
  await dialog.getByTestId('scope-credits').fill('0')
  await expect(dialog.getByTestId('scope-credits-error')).toContainText('1부터 100,000,000까지의 정수')
  await expect(dialog.getByRole('button', { name: '공개하기' })).toBeDisabled()
  await dialog.getByTestId('scope-credits').fill('3000')
  await expect(dialog.getByTestId('scope-credits-error')).toHaveCount(0)
  await shot(page, 'publish-dialog-link', viewport(testInfo))
  await dialog.getByRole('button', { name: '공개하기' }).click()
  await expect(dialog.getByTestId('publish-requested')).toContainText('링크 공개는 관리자가 승인하면 열립니다')
  const sent = writes.find((w) => w.method === 'POST' && w.path === '/publications')?.body as Record<string, unknown>
  expect(sent).toMatchObject({ visibility: 'link', limits: { creditsPerDay: 3000 } })
  expect(state.unexpected).toEqual([])
})

test('the owner widens the audience from the dashboard and can take the request back', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  await page.goto(`/customize/publications/${PUB}`)
  const scope = page.getByTestId('publication-scope')
  await expect(scope).toBeVisible({ timeout: 15_000 })
  await scope.getByRole('radio', { name: /외부 공개/ }).click()
  await expect(scope.getByRole('button', { name: '승인 요청' })).toBeDisabled()
  await scope.getByTestId('scope-credits').fill('5000')
  await scope.getByRole('button', { name: '승인 요청' }).click()
  await expect(page.getByTestId('publication-review')).toContainText('외부 공개를 관리자가 확인하고 있습니다')
  await shot(page, 'dashboard-requested', viewport(testInfo))
  expect(writes.find((w) => w.path === `/publications/${PUB}/scope`)?.body).toEqual({ visibility: 'external', creditsPerDay: 5000 })
  await page.getByTestId('publication-review').getByRole('button', { name: '요청 취소' }).click()
  await expect(page.getByTestId('publication-review')).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})

test('the owner blocks the visitor of a sent conversation and unblocks them in settings', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  await page.goto(`/customize/publications/${PUB}?tab=conversations`)
  await page.getByTestId('publication-conversations').getByRole('button').first().click({ timeout: 15_000 })
  const visitor = page.getByTestId('publication-visitor')
  await expect(visitor).toContainText('방문자 VIS1AB · 로그인하지 않음')
  await visitor.getByRole('button', { name: '이 방문자 차단' }).click()
  await page.getByRole('dialog', { name: '이 방문자 차단' }).getByRole('button', { name: '이 방문자 차단' }).click()
  await expect(visitor).toContainText('차단됨')
  await page.getByRole('tab', { name: '설정' }).click()
  const blocked = page.getByTestId('publication-blocked')
  await expect(blocked).toContainText('방문자 VIS1AB')
  await shot(page, 'dashboard-blocked', viewport(testInfo))
  await blocked.getByRole('button', { name: '차단 풀기' }).click()
  await expect(blocked).toContainText('차단한 방문자가 없습니다')
  expect(writes.map((w) => `${w.method} ${w.path}`)).toEqual(expect.arrayContaining([
    `POST /publications/${PUB}/conversations/${SID}/block`, `DELETE /publications/${PUB}/visitors/vis1/block`,
  ]))
  expect(state.unexpected).toEqual([])
})

/** A browser without an account: the refresh cookie is refused; anything not mocked is unexpected. */
async function noAccount(page: Page, testInfo: { project: { use: { baseURL?: string } } }) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  await page.context().route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = route.request().method()
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ status: 401, json: { detail: 'unauthorized' } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat'], privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false } })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 501, json: { detail: 'Unmocked fixture request' } })
  })
  return { unexpected }
}

test('a visitor without an account opens a link, reads the notice and asks', async ({ page }, testInfo) => {
  const state = await noAccount(page, testInfo)
  const { writes } = await botRoutes(page, { anonymous: true })
  await page.goto(`/b/${SLUG}`)
  const notice = page.getByTestId('bot-notice')
  await expect(notice).toContainText('로그인하지 않으면 이 브라우저에서만 대화를 이어 볼 수 있습니다', { timeout: 15_000 })
  await expect(notice).toContainText('30일이 지나면 지워집니다')
  // No app around it, no way back into the app, no name of the person behind it.
  await expect(page.getByRole('link', { name: '챗봇 목록으로' })).toHaveCount(0)
  await expect(page.getByText('만든 사람:')).toHaveCount(0)
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/)
  await shot(page, 'anon-notice', viewport(testInfo))
  await notice.getByRole('button', { name: '확인하고 시작' }).click()
  await page.getByLabel('질문 입력').fill('휴학은 언제까지 신청하나요?')
  await page.getByRole('button', { name: '보내기', exact: true }).click()
  await expect(page.getByText('휴학은 학기 개시일 전까지 신청합니다')).toBeVisible()
  await shot(page, 'anon-answer', viewport(testInfo))
  expect(writes.map((w) => `${w.method} ${w.path}`)).toEqual(expect.arrayContaining([
    `POST /b/${SLUG}/consent`, `POST /b/${SLUG}/conversations`, `POST /b/${SLUG}/conversations/${SID}/messages`,
  ]))
  expect(state.unexpected).toEqual([])
})

test('an address that does not open without an account offers to sign in', async ({ page }, testInfo) => {
  const state = await noAccount(page, testInfo)
  await page.route(`**/api/b/members-only-1`, (route) => route.fulfill({ status: 404, json: { detail: 'bot_not_found' } }))
  await page.route(`**/api/b/members-only-1/conversations`, (route) => route.fulfill({ status: 404, json: { detail: 'bot_not_found' } }))
  await page.goto('/b/members-only-1')
  await expect(page.getByText('구성원에게만 열린 챗봇이면 로그인해야 합니다')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '로그인' }).click()
  await expect(page.getByLabel('이메일')).toBeVisible()
  expect(state.unexpected).toEqual([])
})

test('a blocked visitor is told and cannot ask', async ({ page }, testInfo) => {
  await noAccount(page, testInfo)
  await botRoutes(page, { anonymous: true, blocked: true })
  await page.goto(`/b/${SLUG}`)
  await expect(page.getByTestId('bot-blocked')).toContainText('이 챗봇을 쓸 수 없습니다.', { timeout: 15_000 })
  await expect(page.getByRole('button', { name: '확인하고 시작' })).toHaveCount(0)
  await expect(page.getByLabel('질문 입력')).toHaveCount(0)
  await expect(page.getByTestId('bot-paused')).toHaveCount(0)
})

test('anyone finds the bots opened to everyone on the public list', async ({ page }, testInfo) => {
  const state = await noAccount(page, testInfo)
  await botRoutes(page, { anonymous: true })
  await page.goto('/b')
  await expect(page.getByRole('heading', { level: 1, name: '공개 챗봇' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('public-bot-list')).toContainText('학사 안내')
  await expect(page.getByTestId('public-bot-list')).not.toContainText('김담당')
  await shot(page, 'public-list', viewport(testInfo))
  await page.getByTestId('public-bot-list').getByRole('link').first().click()
  await expect(page).toHaveURL(new RegExp(`/b/${SLUG}$`))
  expect(state.unexpected).toEqual([])
})

test('an administrator reviews a request and refuses it with a reason', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo, { admin: true })
  const { writes } = await botRoutes(page)
  await page.goto('/admin/publications')
  const list = page.getByTestId('admin-publications')
  await expect(list).toContainText('링크 공개 요청', { timeout: 15_000 })
  await shot(page, 'admin-queue', viewport(testInfo))
  await list.getByRole('button').first().click()
  await expect(page.getByTestId('admin-review-agent')).toContainText('학칙에서 찾아 근거 조항과 함께 답한다.')
  await expect(page.getByTestId('admin-review-knowledge')).toContainText('휴대전화 번호 1')
  await expect(page.getByTestId('admin-review-request')).toContainText('하루 최대 3,000크레딧')
  // Tools by their screen names, what changed against the version answering now, the material.
  await expect(page.getByTestId('admin-review-agent')).toContainText('계산기, 자료 찾기')
  await expect(page.getByTestId('admin-review-changes')).toContainText('근거 조항과 함께')
  await page.getByText('내용 보기 (개인정보는 가림)').click()
  await expect(page.getByTestId('admin-review-preview')).toContainText('제12조(휴학)')
  await shot(page, 'admin-review', viewport(testInfo))
  await page.getByTestId('admin-review-request').getByRole('button', { name: '반려' }).click()
  const modal = page.getByRole('dialog', { name: '반려' })
  await expect(modal.getByRole('button', { name: '반려' })).toBeDisabled()
  await modal.getByTestId('admin-review-reason').fill('자료에 개인 연락처가 있습니다')
  await modal.getByRole('button', { name: '반려' }).click()
  await expect(modal).toHaveCount(0)
  expect(writes.find((w) => w.path === `/admin/publications/${PUB}/review`)?.body).toEqual({ decision: 'reject', reason: '자료에 개인 연락처가 있습니다' })
  expect(state.unexpected).toEqual([])
})

test('without an internal model the dialog says why and publishes nothing', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  await page.route('**/api/publications/preflight', (route) => route.fulfill({ json: preflight({ ok: false, model: null, problems: ['no_local_model'] }) }))
  await page.goto(`/customize/agents/${AGENT}`)
  await expect(page.getByTestId('item-detail-header')).toBeVisible({ timeout: 15_000 })
  // The detail's main action on one's own chat agent, not hidden under 더 보기.
  await page.getByRole('button', { name: '챗봇으로 공개' }).filter({ visible: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '챗봇으로 공개' })
  await expect(dialog.getByTestId('publish-problem')).toContainText('내부 모델이 없어')
  await expect(dialog.getByRole('button', { name: '공개하기' })).toBeDisabled()
})

test('a member reads the notice, asks, rates and sends the conversation to the maker', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  await page.goto(`/b/${SLUG}`)
  const notice = page.getByTestId('bot-notice')
  await expect(notice).toContainText('입력 내용은 이 챗봇을 만든 사람 계정으로 처리·기록됩니다(개선을 위해 개인정보를 가린 통계로만 보입니다).')
  await expect(notice).toContainText('개인정보를 입력하지 마세요.')
  await expect(page.getByLabel('질문 입력')).toBeDisabled()
  await shot(page, 'bot-notice', viewport(testInfo))
  await notice.getByRole('button', { name: '확인하고 시작' }).click()
  await expect(page.getByTestId('bot-prompts')).toBeVisible()
  await shot(page, 'bot-empty', viewport(testInfo))
  await page.getByLabel('질문 입력').fill('휴학은 언제까지 신청하나요?')
  await page.getByRole('button', { name: '보내기', exact: true }).click()
  await expect(page.getByText('휴학은 학기 개시일 전까지 신청합니다')).toBeVisible()
  await expect(page.getByText('학칙.pdf · 제12조(휴학) · 3쪽 · 시행 2025. 3. 1.')).toBeVisible()
  await expect(page.getByTestId('bot-steps')).toContainText('자료 찾기')
  await page.getByRole('button', { name: '아쉬워요' }).click()
  await expect(page.getByTestId('bot-offer-share')).toBeVisible()
  await shot(page, 'bot-answer', viewport(testInfo))
  await page.getByTestId('bot-offer-share').getByRole('button', { name: '보내기' }).click()
  await expect(page.getByTestId('bot-shared')).toContainText('만든 사람에게 보냈습니다')
  expect(writes.map((w) => `${w.method} ${w.path}`)).toEqual(expect.arrayContaining([
    `POST /b/${SLUG}/consent`, `POST /b/${SLUG}/conversations`, `POST /b/${SLUG}/conversations/${SID}/messages`,
    `PATCH /b/${SLUG}/messages/m2/rating`, `POST /b/${SLUG}/conversations/${SID}/share`,
  ]))
  expect(writes.find((w) => w.path.endsWith('/rating'))?.body).toEqual({ rating: 'down' })
  expect(state.unexpected).toEqual([])
})

test('a paused bot says so and takes no question', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  await botRoutes(page, { paused: true })
  await page.goto(`/b/${SLUG}`)
  await expect(page.getByTestId('bot-paused')).toContainText('지금은 이 챗봇을 쓸 수 없습니다.')
  // Nothing to agree to or type into while it cannot answer: the notice alone.
  await expect(page.getByTestId('bot-notice')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '확인하고 시작' })).toHaveCount(0)
  await expect(page.getByLabel('질문 입력')).toHaveCount(0)
  await expect(page.getByTestId('bot-prompts')).toHaveCount(0)
  await shot(page, 'bot-paused', viewport(testInfo))
})

test('the dashboard shows statistics, sent conversations and versions, and rolls back', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  await page.goto(`/customize/publications/${PUB}`)
  await expect(page.getByRole('heading', { level: 1, name: '학사 안내' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('publication-url')).toContainText(`/b/${SLUG}`)
  await shot(page, 'dashboard-overview', viewport(testInfo))
  await page.getByRole('tab', { name: '통계' }).click()
  await expect(page.getByTestId('publication-topics')).toContainText('휴학')
  await expect(page.getByTestId('publication-unanswered')).toContainText('[전화번호]')
  await shot(page, 'dashboard-stats', viewport(testInfo))
  await page.getByRole('tab', { name: '보낸 대화' }).click()
  await page.getByTestId('publication-conversations').getByRole('button').first().click()
  await expect(page.getByTestId('publication-transcript')).toContainText('[전화번호]')
  await shot(page, 'dashboard-conversations', viewport(testInfo))
  await page.getByRole('tab', { name: '버전' }).click()
  await expect(page.getByTestId('publication-versions')).toContainText('2판')
  await page.getByRole('button', { name: '이 판으로 되돌리기' }).click()
  await expect(page.getByTestId('publication-versions').getByText('지금 판')).toHaveCount(1)
  await shot(page, 'dashboard-versions', viewport(testInfo))
  await page.getByRole('tab', { name: '설정' }).click()
  await page.getByRole('switch', { name: '대화 열람' }).click()
  await expect(page.getByText('복사를 허용하면 구성원이 지시문까지 내 에이전트로 가져갈 수 있습니다.')).toBeVisible()
  await page.getByRole('switch', { name: '복사 허용' }).click()
  await expect(page.getByText('저장하면 방문자가 다음 질문 전에 바뀐 안내를 다시 확인합니다.')).toBeVisible()
  await shot(page, 'dashboard-settings', viewport(testInfo))
  await Promise.all([
    page.waitForRequest((r) => r.method() === 'PATCH' && r.url().endsWith(`/publications/${PUB}`)),
    page.getByRole('button', { name: '저장', exact: true }).click(),
  ])
  expect(writes.find((w) => w.method === 'PATCH')?.body).toMatchObject({ conversationView: 'on', allowCopy: true })
  expect(writes.some((w) => w.path.endsWith('/versions/v1/activate'))).toBe(true)
  expect(state.unexpected).toEqual([])
})

test('members find published agents among the shared agents, open one and copy it', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  // The old members' list lands on the shared group.
  await page.goto('/bots')
  await expect(page).toHaveURL(/\/customize\?tab=agents&view=explore$/)
  const group = page.getByTestId('shared-agents')
  await expect(group.getByRole('heading', { name: '공유된 에이전트' })).toBeVisible({ timeout: 15_000 })
  const card = group.locator(`[data-item="bot:${SLUG}"]`)
  await expect(card).toContainText('학사 안내')
  await expect(card).toContainText('제작: 학사지원팀 김담당')
  // Org-shared agents sit in the same group; one also published shows once; the caller's own bot not at all.
  await expect(group.locator('[data-item="agent:a5555555555555555555555555555555"]')).toBeVisible()
  await expect(group.getByRole('button', { name: '예산 검토', exact: true })).toHaveCount(1)
  await expect(group).not.toContainText('내 안내 챗봇')
  // No separate 챗봇 menu; the owner's bot news sits on 사용자 지정.
  await expect(page.getByRole('link', { name: '챗봇', exact: true })).toHaveCount(0)
  await shot(page, 'shared-agents', viewport(testInfo))

  await card.getByRole('button', { name: '학사 안내 추가' }).click()
  await expect(card.getByLabel('학사 안내 추가됨')).toBeVisible()
  expect(writes.some((w) => w.method === 'POST' && w.path === `/bots/${SLUG}/copy`)).toBe(true)
  // A locked bot offers 바로 쓰기 only: no 추가 button, a lock that says why.
  const locked = group.locator('[data-item="bot:rules-4q"]')
  await expect(locked.getByRole('button', { name: '규정 도우미 추가' })).toHaveCount(0)
  await expect(locked.getByLabel('규정 도우미 복사 잠김')).toHaveAttribute('title', '만든 사람이 복사를 허용하지 않아 바로 쓰기만 할 수 있습니다')
  await expect(locked.getByRole('button', { name: '규정 도우미 바로 쓰기' })).toBeVisible()

  await card.getByRole('button', { name: '학사 안내 바로 쓰기' }).click()
  await expect(page).toHaveURL(new RegExp(`/b/${SLUG}$`))
  // Back from the bot page: the shared agents, not a chatbot list.
  await expect(page.getByRole('link', { name: '공유된 에이전트로' })).toHaveAttribute('href', '/customize?tab=agents&view=explore')
  expect(state.unexpected).toEqual([])
})

/** Visible text and labels still in Korean, user content aside. */
async function korean(page: Page, allowed: string[]): Promise<string[]> {
  await settle(page)
  return page.evaluate((allowed) => {
    const out: string[] = []
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const text = (n.textContent || '').trim()
      const el = n.parentElement
      if (!text || !/[가-힣]/.test(text) || !el) continue
      const r = el.getBoundingClientRect()
      if (r.width === 0 && r.height === 0) continue
      if (allowed.some((a) => text.includes(a))) continue
      out.push(text)
    }
    for (const el of Array.from(document.querySelectorAll('[aria-label],[placeholder],[title]'))) {
      for (const attr of ['aria-label', 'placeholder', 'title']) {
        const text = el.getAttribute(attr) || ''
        if (/[가-힣]/.test(text) && !allowed.some((a) => text.includes(a))) out.push(`[${attr}] ${text}`)
      }
    }
    return out
  }, allowed)
}

test('the publishing screens read in English', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await page.addInitScript(() => localStorage.setItem('kchat-lang', 'en'))
  await hubFixture(page, testInfo)
  await botRoutes(page, { english: true })
  // Fixture content in Korean that the screens show as data, not as their own words.
  const data = ['[전화번호]', '학칙.pdf', '제12조', '휴학', '[1]', '내부 Qwen', '논문 리뷰어', '학칙', '학사']
  // A line of Korean that is not data; the language toggle's 「한」 is a mark, not a word.
  const untranslated = (text: string) =>
    text.split('\n').filter((line) => /[가-힣]/.test(line) && line.trim() !== '한' && !data.some((d) => line.includes(d)))

  await page.goto(`/b/${SLUG}`)
  await expect(page.getByTestId('bot-notice')).toBeVisible({ timeout: 15_000 })
  expect(await korean(page, data)).toEqual([])
  await page.getByRole('button', { name: 'Got it, start' }).click()
  await expect(page.getByTestId('bot-prompts')).toBeVisible()
  expect(await korean(page, data)).toEqual([])
  await shot(page, 'en-bot', viewport(testInfo))

  await page.goto('/bots')
  await expect(page.getByRole('heading', { name: 'Shared agents' })).toBeVisible({ timeout: 15_000 })
  // The sidebar and shell are checked by their own gates; the group's own words and the bot's card here.
  const group = page.getByTestId('shared-agents')
  await expect(group.locator(`[data-item="bot:${SLUG}"]`)).toContainText('By: Jane')
  expect(untranslated(await group.locator(`[data-item="bot:${SLUG}"]`).innerText())).toEqual([])

  await page.goto(`/customize/publications/${PUB}`)
  await expect(page.getByRole('heading', { level: 1, name: 'Campus Help' })).toBeVisible({ timeout: 15_000 })
  await settle(page)
  expect(untranslated(await page.getByRole('main').innerText()), 'Overview').toEqual([])
  for (const tab of ['Statistics', 'Sent conversations', 'API', 'Versions', 'Settings']) {
    await page.getByRole('tab', { name: tab }).click()
    await settle(page)
    expect(untranslated(await page.getByRole('main').innerText()), tab).toEqual([])
  }

  await page.goto(`/customize/agents/${AGENT}`)
  await expect(page.getByTestId('item-detail-header')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Publish as a chatbot' }).filter({ visible: true }).first().click()
  const dialog = page.getByRole('dialog', { name: 'Publish as a chatbot' })
  await expect(dialog.getByTestId('publish-model')).toBeVisible()
  await dialog.getByRole('radio', { name: /Anyone with the link/ }).click()
  expect(untranslated(await dialog.innerText())).toEqual([])
})

test('the screens without an account and the review read in English', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await page.addInitScript(() => localStorage.setItem('kchat-lang', 'en'))
  await noAccount(page, testInfo)
  await botRoutes(page, { english: true, anonymous: true })
  const data = ['[전화번호]', '학칙', '휴학']
  await page.goto(`/b/${SLUG}`)
  await expect(page.getByTestId('bot-notice')).toBeVisible({ timeout: 15_000 })
  expect(await korean(page, data)).toEqual([])
  await page.goto('/b')
  await expect(page.getByRole('heading', { level: 1, name: 'Published chatbots' })).toBeVisible({ timeout: 15_000 })
  expect(await korean(page, data)).toEqual([])
})

test('the administrator review reads in English', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await page.addInitScript(() => localStorage.setItem('kchat-lang', 'en'))
  await hubFixture(page, testInfo, { admin: true })
  await botRoutes(page, { english: true })
  const data = ['[전화번호]', '학칙', '휴학', '학사', '김담당']
  const untranslated = (text: string) =>
    text.split('\n').filter((line) => /[가-힣]/.test(line) && line.trim() !== '한' && !data.some((d) => line.includes(d)))
  await page.goto(`/admin/publications/${PUB}`)
  await expect(page.getByTestId('admin-review-agent')).toBeVisible({ timeout: 15_000 })
  expect(untranslated(await page.getByRole('main').innerText())).toEqual([])
})

/* ── channels ──────────────────────────────────────────────────────────── */

test('the owner asks for the API channel and makes a key shown once', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page, { pub: { visibility: 'link', limits: { ...limits, creditsPerDay: 3000 } } })
  await page.goto(`/customize/publications/${PUB}`)
  const channels = page.getByTestId('publication-channels')
  await expect(channels).toBeVisible({ timeout: 15_000 })
  // Framing needs the bot listed for everyone; a link bot cannot switch it on.
  await expect(channels.getByTestId('channel-embed')).toContainText('이 공개 범위에서는 쓸 수 없음')
  await expect(channels.getByTestId('channel-embed').getByRole('switch')).toBeDisabled()
  await expect(page.getByTestId('publication-admin-reads')).toContainText('관리자가 열람함')
  await expect(page.getByTestId('publication-admin-reads')).toContainText('신고 확인')
  await channels.getByTestId('channel-api').getByRole('switch').click()
  await expect(channels.getByTestId('channels-save')).toHaveText(/승인 요청/)
  await shot(page, 'channels-ask', viewport(testInfo))
  await channels.getByTestId('channels-save').click()
  await expect(page.getByTestId('publication-review')).toContainText('채널 요청을 관리자가 확인하고 있습니다')
  await expect(page.getByTestId('channel-api')).toContainText('승인 대기')
  expect(writes.find((w) => w.path === `/publications/${PUB}/channels`)?.body).toEqual({ embed: { on: false, origins: [] }, api: true, mcp: false })

  await page.getByRole('tab', { name: 'API' }).click()
  const api = page.getByTestId('publication-api')
  await expect(api).toContainText('API·MCP 채널이 아직 열리지 않았습니다')
  await api.getByLabel('키 이름').fill('학과 홈페이지 서버')
  await api.getByTestId('key-create').click()
  await expect(page.getByTestId('new-key')).toContainText('kcb_AbCdEfGh')
  await expect(page.getByTestId('new-key')).toContainText('지금 한 번만 보입니다')
  await expect(page.getByTestId('api-curl')).toContainText(`"model": "${SLUG}"`)
  await expect(page.getByTestId('api-curl')).toContainText('kcb_AbCdEfGh')
  await expect(page.getByTestId('api-python')).toContainText('/api/bots/v1')
  await expect(page.getByTestId('mcp-json')).toContainText(`/api/bots/mcp/${SLUG}`)
  await expect(page.getByTestId('channel-usage')).toContainText('챗봇 페이지')
  await shot(page, 'api-tab', viewport(testInfo))
  await page.getByTestId('key-list').getByRole('button', { name: '폐기' }).click()
  await page.getByRole('dialog', { name: '키 폐기' }).getByRole('button', { name: '폐기' }).click()
  await expect(page.getByTestId('key-list')).toHaveCount(0)
  expect(writes.map((w) => `${w.method} ${w.path}`)).toEqual(expect.arrayContaining([
    `POST /publications/${PUB}/keys`, `DELETE /publications/${PUB}/keys/k1`,
  ]))
  expect(state.unexpected).toEqual([])
})

test('an open embed channel gives the line to paste', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await botRoutes(page, { pub: { visibility: 'external', channels: { embed: { origins: [SITE] } }, limits: { ...limits, creditsPerDay: 3000 } } })
  await page.goto(`/customize/publications/${PUB}`)
  const embed = page.getByTestId('channel-embed')
  await expect(embed).toContainText('열림', { timeout: 15_000 })
  await expect(page.getByTestId('channel-origins')).toHaveValue(SITE)
  await expect(page.getByTestId('embed-snippet')).toContainText('/embed/widget.js')
  await expect(page.getByTestId('embed-snippet')).toContainText(`data-bot="${SLUG}"`)
  await shot(page, 'channels-embed', viewport(testInfo))
  expect(state.unexpected).toEqual([])
})

test('the framed page asks without an account and marks every call', async ({ page }, testInfo) => {
  const state = await noAccount(page, testInfo)
  const { botCalls } = await botRoutes(page, { anonymous: true })
  const refreshes: string[] = []
  page.on('request', (r) => r.url().endsWith('/api/auth/refresh') && refreshes.push(r.url()))
  await page.goto(`/embed/b/${SLUG}`)
  const notice = page.getByTestId('bot-notice')
  await expect(notice).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('link', { name: '챗봇 목록으로' })).toHaveCount(0)
  await notice.getByRole('button', { name: '확인하고 시작' }).click()
  await page.getByLabel('질문 입력').fill('휴학은 언제까지 신청하나요?')
  await page.getByRole('button', { name: '보내기', exact: true }).click()
  await expect(page.getByText('휴학은 학기 개시일 전까지 신청합니다')).toBeVisible()
  await shot(page, 'embed-answer', viewport(testInfo))
  expect(botCalls.length).toBeGreaterThan(3)
  expect(botCalls.every((c) => c.embed === '1' && !c.auth)).toBe(true)
  // A frame never signs in, so it never asks for the refresh cookie.
  expect(refreshes).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('the loader on another page opens the bot in a frame, the whole screen on a phone', { tag: '@phone' }, async ({ page }, testInfo) => {
  await noAccount(page, testInfo)
  await botRoutes(page, { anonymous: true })
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  await page.setContent(`<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><h1>학과 홈페이지</h1>
    <script src="${origin}/embed/widget.js" data-bot="${SLUG}"></script></body></html>`)
  const launcher = page.getByRole('button', { name: '챗봇에게 묻기' })
  await expect(launcher).toBeVisible({ timeout: 15_000 })
  await launcher.click()
  const frame = page.locator('iframe[title="챗봇에게 묻기"]')
  await expect(frame).toHaveAttribute('src', `${origin}/embed/b/${SLUG}`)
  await expect(page.frameLocator('iframe[title="챗봇에게 묻기"]').getByTestId('bot-notice')).toBeVisible({ timeout: 15_000 })
  const box = await frame.boundingBox()
  const size = page.viewportSize()
  if (viewport(testInfo) === 'phone') {
    expect(Math.round(box?.width ?? 0)).toBe(size?.width)
    // The close button sits above the frame, not over the bot's own header.
    const close = await page.getByRole('button', { name: '챗봇에게 묻기' }).boundingBox()
    expect((close?.y ?? 0) + (close?.height ?? 0)).toBeLessThanOrEqual(box?.y ?? 0)
  } else expect(Math.round(box?.width ?? 0)).toBe(400)
  await shot(page, 'widget-open', viewport(testInfo))
  // Esc pressed inside the frame closes it too.
  await page.frameLocator('iframe[title="챗봇에게 묻기"]').getByTestId('bot-notice').click()
  await page.keyboard.press('Escape')
  await expect(frame).toBeHidden()
  await expect(launcher).toHaveAttribute('aria-expanded', 'false')
})

test('the loader on a site the bot was not approved for says so instead of an empty panel', async ({ page }, testInfo) => {
  test.setTimeout(60_000)
  await noAccount(page, testInfo)
  await botRoutes(page, { anonymous: true })
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  // The browser refuses the frame (frame-ancestors): here, a page that never says it drew.
  await page.route(`${origin}/embed/b/**`, (route) => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>x</title>' }))
  await page.setContent(`<!doctype html><html><body><h1>다른 사이트</h1>
    <script src="${origin}/embed/widget.js" data-bot="${SLUG}"></script></body></html>`)
  await page.getByRole('button', { name: '챗봇에게 묻기' }).click({ timeout: 15_000 })
  await expect(page.getByRole('status')).toContainText('이 사이트에서는 챗봇을 열 수 없습니다', { timeout: 15_000 })
  await expect(page.locator('iframe[title="챗봇에게 묻기"]')).toBeHidden()
})

test('an administrator reads conversations only after giving a reason', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo, { admin: true })
  await botRoutes(page)
  const asked: string[] = []
  page.on('request', (r) => r.url().includes(`/admin/publications/${PUB}/conversations`) && asked.push(r.url()))
  await page.goto(`/admin/publications/${PUB}`)
  const card = page.getByTestId('admin-conversations')
  await expect(card).toBeVisible({ timeout: 15_000 })
  await card.getByTestId('admin-conversations-open').click()
  const modal = page.getByRole('dialog', { name: '대화 열람' })
  await expect(modal.getByTestId('admin-conversations-confirm')).toBeDisabled()
  await modal.getByTestId('admin-conversations-reason').fill('남용 신고 확인')
  await modal.getByTestId('admin-conversations-confirm').click()
  await expect(page.getByTestId('admin-conversation-list')).toContainText('API·MCP 키')
  await page.getByTestId('admin-conversation-list').getByRole('button').first().click()
  await expect(page.getByTestId('admin-conversation-transcript')).toContainText('[전화번호]')
  await shot(page, 'admin-read', viewport(testInfo))
  expect(asked.length).toBe(2)
  expect(asked.every((u) => new URL(u).searchParams.get('reason') === '남용 신고 확인')).toBe(true)
  expect(state.unexpected).toEqual([])
})

test('a deep link to an agent loads each workspace list once', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto(`/customize/agents/${AGENT}`)
  await expect(page.getByTestId('item-detail-header')).toBeVisible({ timeout: 15_000 })
  await page.waitForLoadState('networkidle')
  // The sign-in load and the page's own share one request per list.
  for (const list of ['/agents', '/projects', '/skills', '/memory']) {
    expect(state.reads.filter((path) => path === list), list).toHaveLength(1)
  }
})
