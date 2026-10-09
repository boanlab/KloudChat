import type { Page, TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * The 「사용자 지정」 hub against fixture data only: one account with its own agents, skills and
 * workflows, a store of shared ones, and a tool catalogue with one connector. Every write is
 * recorded in `calls`; anything unmocked lands in `unexpected`.
 */

export const at = '2026-10-08T07:00:00.000Z'
const older = '2026-09-01T07:00:00.000Z'
export const ME = 'fixture-user'
export const newSessionId = 'b5555555555555555555555555555555'

export function agentRow(id: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    ownerId: ME, ownerName: 'Fixture', id, name, slug: id.slice(0, 8), description: `${name} 에이전트입니다.`, model: '',
    systemPrompt: `너는 ${name} 담당이다.\n\n- 근거를 붙인다\n- 짧게 답한다`, tools: null, skillIds: null, kinds: ['chat'],
    surface: null, renderTemplateId: null, docFormat: null, designSystemId: null, guide: '', starters: [], shareMode: 'open',
    sealed: false, temperature: 0.5, color: '#168267', enabled: true, visibility: 'private', installs: 0, catalogKey: null,
    originId: null, official: false, installed: false, runs: 4, hasKnowledge: false, updatedAt: at, ...extra,
  }
}

export function skillRow(id: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    id, name, slug: id, description: `${name} 절차입니다.`, whenToUse: `${name}이 필요할 때`, body: `# ${name}\n\n1. 자료를 읽는다\n2. 정리한다`,
    catalogKey: null, source: 'personal', kinds: ['chat', 'report', 'slides'], requiredTools: [], estimatedTokens: 320,
    version: '1.0.0', enabled: true, visibility: 'private', installs: 0, originId: null, updatedAt: at, scripts: [], ...extra,
  }
}

function pipelineRow(id: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    id, name, description: `${name} 순서로 일합니다.`, base: null, originId: null, visibility: 'private', review: '', reviewNote: '',
    reviewedAt: null, version: 1, installs: 0, ownerId: ME, ownerName: 'Fixture', boundAgents: [], createdAt: at, updatedAt: at,
    surface: 'report', problems: [], warnings: [],
    stages: [{ id: 's1', kind: 'human', ref: 'gaps', settings: {}, title: '확인 질문' }], ...extra,
  }
}

export const myAgents = [
  agentRow('a1111111111111111111111111111111', '논문 리뷰어', {
    guide: '논문 PDF 를 첨부하고 학회 이름을 알려 주세요.', starters: ['첨부한 논문을 리뷰어 관점에서 검토해 줘'],
    kinds: ['chat', 'report'], runs: 12, visibility: 'org',
  }),
  agentRow('a2222222222222222222222222222222', '회의록 정리', { enabled: false, runs: 0 }),
]
export const storeAgents = [
  agentRow('a3333333333333333333333333333333', '비교 보고서', {
    ownerId: 'admin', ownerName: '관리자', official: true, visibility: 'org', installs: 42, catalogKey: 'comparison-report',
    kinds: ['report'], surface: 'report', description: '두 안을 기준별로 비교해 보고서를 씁니다.', updatedAt: older,
  }),
  agentRow('a4444444444444444444444444444444', '보도자료 초안', {
    ownerId: 'u2', ownerName: '김동료', visibility: 'org', installs: 7, kinds: ['report'], shareMode: 'sealed', systemPrompt: '',
  }),
  agentRow('a5555555555555555555555555555555', '예산 검토', {
    ownerId: 'u3', ownerName: '이동료', visibility: 'org', installs: 0, description: '예산안의 단가와 합계를 검토합니다.',
  }),
]

export const mySkills = [
  skillRow('k1111111111111111111111111111111', '데이터 분석', { source: 'built-in', catalogKey: 'data-analysis' }),
  skillRow('k2222222222222222222222222222222', '주간 현황 보고', { visibility: 'org' }),
  skillRow('k3333333333333333333333333333333', '번역 검수', { enabled: false }),
]
export const storeSkills = [
  { ...skillRow('k4444444444444444444444444444444', '견적 계산'), ownerId: 'admin', ownerName: '관리자', official: true, installed: false, installs: 31, visibility: 'org' },
  { ...skillRow('k5555555555555555555555555555555', '보도자료 교정'), ownerId: 'u2', ownerName: '김동료', official: false, installed: false, installs: 3, visibility: 'org' },
]

export const myPipelines = [pipelineRow('p1111111111111111111111111111111', '비교 → 요약', { review: 'pending' })]
export const storePipelines = [
  pipelineRow('p2222222222222222222222222222222', '분석 → 비교 → 개조식', {
    ownerId: 'u2', ownerName: '김동료', review: 'approved', visibility: 'org', installs: 5, mine: false, installed: false,
  }),
]

export const tools = [
  { name: 'web_search', label: '웹 검색', available: true, group: '기본 도구', title: '웹 검색', description: '웹에서 최신 자료를 찾아 출처와 함께 답합니다.' },
  { name: 'calculate', label: '수치 계산', available: true, group: '기본 도구', title: '수치 계산', description: '사칙연산과 거듭제곱을 정확히 계산합니다.' },
  { name: 'mcp__github__list_open_issues', label: 'GitHub · list open issues', available: true, group: 'GitHub', title: 'list open issues', description: 'Lists open issues in a repository.' },
  { name: 'search_knowledge', label: '에이전트 지식이 있을 때 검색', available: false, group: '기본 도구', title: '자료 찾기', description: '에이전트에 붙여 둔 자료에서 찾습니다.' },
]

const templates = [
  { id: 'tpl-report', kind: 'document', surface: 'report', name: '비교 보고서', nameEn: 'Comparison report', category: '보고', categoryEn: 'Report',
    description: '두 안을 기준별로 비교합니다.', descriptionEn: 'Compares two options.', fills: ['비교 대상'], fillsEn: ['Options'], checks: [],
    hasPreview: false, slideCount: 0 },
  { id: 'tpl-deck', kind: 'deck', surface: 'slides', name: '사업 소개', nameEn: 'Pitch', category: '발표', categoryEn: 'Talk',
    description: '사업을 10장으로 소개합니다.', descriptionEn: 'Ten slides.', fills: [], fillsEn: [], checks: [], hasPreview: false, slideCount: 0 },
]

const designs = [
  { id: 'd1', name: '학과 공식 문서', description: '남색 강조', tokens: { accent: '#1f3a8a', ink: '#111111', muted: '#666666', font: 'serif' },
    body: '', imageStyle: '', craft: [], shared: false, mine: true, updatedAt: at },
  { id: 'd2', name: '기관 공용', description: '공용 디자인', tokens: { accent: '#0e8a5f', ink: '#111111', muted: '#666666', font: 'gothic' },
    body: '', imageStyle: '', craft: [], shared: true, mine: false, updatedAt: at },
]

/** `n` conversations, newest first, a minute apart; the first is pinned. */
export function conversationRows(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: `c${String(i).padStart(31, '0')}`, kind: 'chat', title: `대화 ${i + 1}`, projectId: null, agentId: null,
    model: 'fixture/base', routingMode: 'manual', artifactId: null, pending: null, skillIds: [], renderTemplateId: null,
    pinned: i === 0, createdAt: at, updatedAt: new Date(Date.parse(at) - i * 60_000).toISOString(), messages: null,
    preview: null, messageCount: 0, made: null,
  }))
}

export async function hubFixture(page: Page, testInfo: TestInfo, { emptyMine = false, conversations = 0 } = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const problems: string[] = []
  const calls: { method: string; path: string; body: Record<string, unknown> }[] = []
  let made: ReturnType<typeof pipelineRow>[] = []
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  let agents = [...(emptyMine ? [] : myAgents), ...storeAgents]
  let skills = emptyMine ? [] : [...mySkills]
  const store = storeSkills.map((s) => ({ ...s }))
  let sessions: Record<string, unknown>[] = conversationRows(conversations)

  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const body = (() => {
      try {
        return (request.postDataJSON() ?? {}) as Record<string, unknown>
      } catch {
        return {}
      }
    })()
    if (method !== 'GET' && path !== '/auth/refresh') calls.push({ method, path, body })
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })

    if (method === 'POST' && path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: ME, name: 'Fixture', email: 'fixture@example.test', role: 'user', status: 'active', monthlyCredits: 1000,
        creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
        preferences: { autoMemory: false, showUsage: false, streamResponses: true } } })
    if (method === 'GET' && path === '/auth/config') return json({ brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report', 'slides'], privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false })
    if (method === 'GET' && path === '/models') return json({ models: [{ id: 'fixture/base', label: 'Fixture', name: 'Fixture',
      vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report', 'slides'], modality: 'chat', dataBoundary: 'external',
      creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }], defaultChatModel: 'fixture/base',
      litellmAvailable: false, autoRouting: { enabled: false, available: false } })
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (method === 'GET' && path === '/agents') return json(agents)
    if (method === 'GET' && path === '/skills') return json(skills)
    if (method === 'GET' && path === '/skills/store') return json(store)
    if (method === 'GET' && path === '/tools') return json(tools)
    if (method === 'GET' && path === '/designs') return json(designs)
    if (method === 'GET' && path === '/design-templates') return json(templates)
    if (method === 'GET' && path === '/user-pipelines') return json(emptyMine ? [] : myPipelines)
    if (method === 'GET' && path === '/user-pipelines/store') return json(storePipelines)
    if (method === 'GET' && path === '/user-pipelines/catalogue') return json({ maxSteps: 12, skills: [], checks: [], human: [
      { ref: 'gaps', name: '확인 질문', description: '' }], exportFormats: ['docx'], reviewModels: [] })
    if (method === 'GET' && /^\/user-pipelines\/[^/]+$/.test(path)) {
      const row = [...myPipelines, ...made].find((p) => path.endsWith(p.id))
      return row ? json(row) : json({ detail: 'pipeline_not_found' }, 404)
    }
    if (method === 'GET' && /^\/agents\/[^/]+\/pipeline$/.test(path)) return json({ name: 'comparison-report', formats: [], agents: [],
      options: {}, checks: [], flows: { write: [{ name: 'draft', label: '초안', kind: 'model' }] } })
    if (method === 'GET' && /^\/agents\/[^/]+\/knowledge/.test(path)) return json([])
    if (method === 'GET' && path === '/sessions') return json(sessions)
    if (method === 'GET' && path === '/sessions/paged') return json({ items: sessions.slice(0, 50), total: sessions.length, page: 1, size: 50 })
    if (method === 'GET' && path.startsWith('/sessions/')) {
      const row = sessions.find((s) => path.startsWith(`/sessions/${s.id}`))
      if (path.endsWith('/messages') || path.endsWith('/jobs')) return json([])
      return row ? json({ ...row, messages: [] }) : json({ detail: 'not_found' }, 404)
    }
    if (method === 'POST' && path === '/sessions') {
      const row = { id: newSessionId, kind: body.kind, title: '새 대화', projectId: null, agentId: body.agentId ?? null,
        model: 'fixture/base', routingMode: 'manual', artifactId: null, pending: null, skillIds: [], renderTemplateId: null,
        pinned: false, createdAt: at, updatedAt: at, messages: null, preview: null, messageCount: 0, made: null }
      sessions = [row]
      return json(row, 201)
    }
    if (method === 'PATCH' && path.startsWith('/sessions/')) {
      sessions = sessions.map((s) => ({ ...s, ...body }))
      return json({ ...sessions[0], messages: null })
    }
    if (method === 'POST' && /^\/user-pipelines\/[^/]+\/start$/.test(path)) {
      sessions = [{ id: newSessionId, kind: 'report', title: '워크플로', projectId: null, agentId: null, model: 'fixture/base',
        routingMode: 'manual', artifactId: null, pending: null, skillIds: [], renderTemplateId: null, pinned: false, createdAt: at,
        updatedAt: at, messages: null, preview: null, messageCount: 0, made: null }]
      return json({ sessionId: newSessionId, kind: 'report', pipelineId: path.split('/')[2] }, 201)
    }
    if (method === 'POST' && path === '/user-pipelines') {
      const row = pipelineRow('p9999999999999999999999999999999', '새 워크플로', { description: '', stages: [], version: 1 })
      made.push(row)
      return json(row, 201)
    }
    if (method === 'DELETE' && /^\/user-pipelines\/[^/]+$/.test(path)) {
      made = made.filter((p) => !path.endsWith(p.id))
      return route.fulfill({ status: 204 })
    }
    if (method === 'POST' && /^\/skills\/[^/]+\/install$/.test(path)) {
      const id = path.split('/')[2]
      const origin = store.find((s) => s.id === id)!
      origin.installed = true
      const copy = skillRow(`copy-${id}`.slice(0, 32), origin.name, { originId: id })
      skills = [copy, ...skills]
      return json(copy)
    }
    if (method === 'POST' && /^\/agents\/[^/]+\/install$/.test(path)) {
      const id = path.split('/')[2]
      const origin = agents.find((a) => a.id === id)!
      const copy = agentRow(`copy-${id}`.slice(0, 32), origin.name, { originId: id })
      agents = [copy, ...agents.map((a) => (a.id === id ? { ...a, installed: true, installs: a.installs + 1 } : a))]
      return json(copy)
    }
    if (method === 'POST' && path === '/skills') {
      const made = skillRow('k9999999999999999999999999999999', String(body.name), { ...body })
      skills = [made, ...skills]
      return json(made, 201)
    }
    if (method === 'PATCH' && /^\/skills\/[^/]+$/.test(path)) {
      const id = path.split('/')[2]
      skills = skills.map((s) => (s.id === id ? { ...s, ...body } : s))
      return json(skills.find((s) => s.id === id))
    }
    if (method === 'POST' && /^\/skills\/[^/]+\/toggle$/.test(path)) {
      const id = path.split('/')[2]
      skills = skills.map((s) => (s.id === id ? { ...s, enabled: !s.enabled } : s))
      return json(skills.find((s) => s.id === id))
    }
    if (method === 'POST' && path === '/agents') {
      const made = agentRow('a9999999999999999999999999999999', String(body.name), { ...body })
      agents = [made, ...agents]
      return json(made, 201)
    }
    if (method === 'PATCH' && /^\/agents\/[^/]+$/.test(path)) {
      const id = path.split('/')[2]
      agents = agents.map((a) => (a.id === id ? { ...a, ...body } : a))
      return json(agents.find((a) => a.id === id))
    }
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/projects', '/memory', '/connectors', '/connectors/catalog', '/jobs', '/shares', '/artifacts',
      '/history'].includes(path)) return json([])
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'not_found' }, 404)
  })
  return { unexpected, problems, calls }
}
