import type { Page, TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * The composer against fixture data only: chat, report and slides surfaces, two chat models
 * (one strict-local), three skills, two agents, one connected connector and a 양식 per document
 * surface. Every send body is recorded; anything unmocked lands in `unexpected`.
 */

export const at = '2026-10-09T07:00:00.000Z'
export const chatId = 'c0000000000000000000000000000001'
export const question = '이번 분기 지원 사업 공고를 세 줄로 정리해 줘.'
export const answer = [
  '이번 분기 공고는 세 가지로 정리됩니다.',
  '',
  '1. **신청 기간**은 10월 14일부터 11월 8일까지이며, 온라인 접수만 받습니다. 마감일 18시 이후 제출분은 받지 않습니다.',
  '2. **지원 대상**은 설립 7년 이내 기업으로, 공동 신청은 최대 세 기관까지 허용합니다.',
  '3. **평가**는 서면 70점, 발표 30점이며 발표 대상은 서면 상위 두 배수입니다.',
  '',
  '| 항목 | 내용 |',
  '| --- | --- |',
  '| 지원 규모 | 과제당 최대 2억 원 |',
  '| 수행 기간 | 12개월 |',
].join('\n')

const user = {
  id: 'fixture-user', email: 'fixture@example.test', name: 'UI 검증', role: 'user', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}

function model(id: string, label: string, kinds: string[], extra: Record<string, unknown> = {}) {
  return {
    id, label, name: label, vendor: 'Fixture', provider: 'fixture', kinds, modality: 'chat',
    dataBoundary: 'external', strictLocal: false, privacyOnly: false, creditCost: 1, inputCreditCost: 1,
    supportsTools: true, supportsVision: false, contextWindow: 64000, ...extra,
  }
}

function skill(id: string, name: string) {
  return {
    id, name, slug: id, description: `${name} 절차입니다.`, whenToUse: '', body: `# ${name}`, catalogKey: null,
    source: 'personal', kinds: ['chat', 'report', 'slides'], requiredTools: [], estimatedTokens: 120,
    version: '1', enabled: true, visibility: 'private', installs: 0, originId: null, updatedAt: at, scripts: [],
  }
}

function agent(id: string, name: string) {
  return {
    ownerId: user.id, ownerName: 'Fixture', id, name, slug: id.slice(0, 8), description: `${name} 에이전트입니다.`,
    model: '', systemPrompt: '', tools: null, skillIds: null, kinds: ['chat', 'report', 'slides'], surface: null,
    renderTemplateId: null, docFormat: null, designSystemId: null, guide: '', starters: [], shareMode: 'open',
    sealed: false, temperature: 0.5, color: '#168267', enabled: true, visibility: 'private', installs: 0,
    catalogKey: null, originId: null, official: false, installed: false, runs: 0, hasKnowledge: false, updatedAt: at,
  }
}

/** An agent as `/agents` lists it, for a test that needs more than the fixture's own. */
export function fixtureAgent(id: string, name: string, extra: Record<string, unknown> = {}) {
  return { ...agent(id, name), ...extra }
}

const skillNames = ['초안 검토', '데이터 분석', '번역 검수']
const agentNames = ['논문 리뷰어', '예산 검토']
/** An agent that writes reports only: not offered on the chat screen's 「@」. */
const reportAgent = { ...agent('a9'.padEnd(33, '9'), '회의록 정리'), kinds: ['report'], surface: 'report' }

const templates = [
  { id: 'tpl-report', kind: 'document', surface: 'report', name: '비교 보고서', nameEn: 'Comparison report',
    category: '보고', categoryEn: 'Report', description: '두 안을 기준별로 비교합니다.',
    descriptionEn: 'Compares two options.', fills: [], fillsEn: [], checks: [], hasPreview: false, slideCount: 0 },
  { id: 'tpl-deck', kind: 'deck', surface: 'slides', name: '사업 소개', nameEn: 'Pitch', category: '발표',
    categoryEn: 'Talk', description: '사업을 10장으로 소개합니다.', descriptionEn: 'Ten slides.', fills: [],
    fillsEn: [], checks: [], hasPreview: false, slideCount: 0 },
]

const connector = {
  id: 'conn-1', name: 'GitHub', slug: 'github', description: '저장소 이슈를 읽습니다.', category: '개발',
  transport: 'http', endpoint: 'https://example.test/mcp', auth: 'none', kinds: [], official: true,
  installed: true, enabled: true, status: 'connected', tools: [], lastSyncAt: at, error: null,
}

function sessionRow(id: string, kind: string, messages: Record<string, unknown>[] = []) {
  return {
    id, kind, title: kind === 'chat' ? '지원 사업 공고 정리' : '새 대화', model: 'fixture/model',
    routingMode: 'manual', projectId: null, agentId: null, artifactId: null, pending: null, skillIds: [],
    renderTemplateId: null, pinned: false, messageCount: messages.length, createdAt: at, updatedAt: at,
    messages, preview: null, made: null,
  }
}

type SentTurn = { path: string; body: Record<string, unknown> }

export async function composerFixture(
  page: Page,
  testInfo: TestInfo,
  {
    skills = true,
    agents = true,
    connectors = true,
    extraAgents = [],
  }: { skills?: boolean; agents?: boolean; connectors?: boolean; extraAgents?: Record<string, unknown>[] } = {},
) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const sends: SentTurn[] = []
  const created: Record<string, unknown>[] = []
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  const rows = new Map<string, Record<string, unknown>>()
  rows.set(chatId, sessionRow(chatId, 'chat', [
    { id: 'm0000000000000000000000000000001', role: 'user', content: question, attachments: [], createdAt: at },
    { id: 'm0000000000000000000000000000002', role: 'assistant', content: answer, attachments: [],
      model: 'fixture/model', createdAt: at },
  ]))
  let next = 2

  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    const body = (() => {
      try {
        return (request.postDataJSON() ?? {}) as Record<string, unknown>
      } catch {
        return {}
      }
    })()

    if (path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600, user })
    if (method === 'GET' && path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'slides', 'image', 'av'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    })
    if (method === 'GET' && path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'fixture/model',
      defaults: { chat: 'fixture/model', report: 'fixture/model', slides: 'fixture/model', image: 'fixture/image' },
      models: [
        model('fixture/model', '검증 모델', ['chat', 'report', 'slides']),
        model('fixture/local', '로컬 모델', ['chat'], { strictLocal: true, dataBoundary: 'strict_local' }),
        model('fixture/image', '그림 모델', ['image'], { modality: 'image', creditPerImage: 4, supportsTools: false }),
      ],
      autoRouting: { enabled: false, available: false, reason: null, economyModelIds: [] },
    })
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (method === 'GET' && path === '/skills') return json(skills
      ? skillNames.map((name, i) => skill(`k${String(i + 1).repeat(32)}`, name)) : [])
    if (method === 'GET' && path === '/agents') return json(agents
      ? [...agentNames.map((name, i) => agent(`a${String(i + 1).repeat(32)}`, name)), reportAgent, ...extraAgents]
      : [])
    if (method === 'GET' && path === '/connectors') return json(connectors ? [connector] : [])
    if (method === 'GET' && path === '/design-templates') return json(templates)
    if (method === 'GET' && path === '/sessions') return json([...rows.values()])
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    if (method === 'POST' && path === '/sessions') {
      // An unsent conversation is created under the id it was opened with.
      const id = typeof body.id === 'string' ? body.id : `c${String(next++).padStart(31, '0')}`
      const row = sessionRow(id, String(body.kind ?? 'chat'))
      Object.assign(row, body.agentId ? { agentId: body.agentId } : {})
      rows.set(id, row)
      created.push(body)
      return json(row)
    }
    const sessionMatch = path.match(/^\/sessions\/([^/]+)$/)
    if (sessionMatch && method === 'GET') {
      const row = rows.get(sessionMatch[1])
      return row ? json(row) : json({ detail: 'not_found' }, 404)
    }
    if (sessionMatch && method === 'PATCH') {
      const row = rows.get(sessionMatch[1])
      if (!row) return json({ detail: 'not_found' }, 404)
      Object.assign(row, body)
      return json(row)
    }
    const sendMatch = path.match(/^\/sessions\/([^/]+)\/messages$/)
    if (sendMatch && method === 'GET') return json((rows.get(sendMatch[1])?.messages as unknown[]) ?? [])
    if (sendMatch && method === 'POST') {
      sends.push({ path, body })
      return route.fulfill({ contentType: 'text/event-stream', body: [
        { type: 'delta', text: '확인했습니다.' },
        { type: 'usage', inputTokens: 4, outputTokens: 4, credits: 0 }, { type: 'done' },
      ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') })
    }
    if (method === 'POST' && path === '/files') {
      return json({ id: `f${String(next++).padStart(31, '0')}`, name: 'notes.txt', size: 12, mime: 'text/plain',
        tokens: 4, projectId: null, sessionId: null, preview: '', error: null, createdAt: at }, 201)
    }
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && (path.endsWith('/jobs') || [
      '/projects', '/artifacts', '/memory', '/tools', '/connectors/catalog', '/designs', '/shares',
      '/skills/store', '/user-pipelines', '/user-pipelines/store', '/jobs',
    ].includes(path))) return json([])
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'Unmocked fixture request' }, 501)
  })
  return { unexpected, sends, created }
}
