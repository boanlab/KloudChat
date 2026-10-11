import type { Page, TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'
import { readFileSync } from 'node:fs'

/** What `/user-templates/catalogue` and `/user-templates/start/doc-minutes` send, made by the API
 * itself (`user_templates.catalogue`, `draft_of`). */
const templateFixture = JSON.parse(readFileSync(new URL('./template-fixture.json', import.meta.url), 'utf8')) as {
  catalogue: Record<string, unknown>
  startMinutes: Record<string, unknown>
}

/**
 * The 「사용자 지정」 hub against fixture data only: one account with its own agents, skills and
 * 묶음, a store of shared ones, the bases an agent starts from, and a tool catalogue with one
 * connector. Every write is recorded in `calls`; anything unmocked lands in `unexpected`.
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

/** What a skill's server fields may name, as `GET /skills/server-catalogue` sends it. */
export const serverCatalogue = {
  postChecks: [
    { ref: 'source-check/post', skill: '출처 점검', summary: '답의 수치·인용이 자료에 있는지 확인합니다.' },
    { ref: 'copy-editing/preserve', skill: '교정·윤문', summary: '원문의 값과 문장이 바뀌지 않았는지 봅니다.' },
    { ref: 'release-notes/notes', skill: '릴리스 노트', summary: '' },
  ],
  entries: {
    pre: [{ ref: 'gap-check/gaps', skill: '빠진 정보 묻기', summary: '' }],
    post: [
      { ref: 'grounding-check/trace', skill: '근거 확인', summary: '' },
      { ref: 'generic-language-killer/scan', skill: '상투어 걷어내기', summary: '' },
    ],
  },
  scripts: [
    { ref: 'date-calc/dates', skill: '날짜 계산', summary: '상대 날짜를 기준일로 풉니다.' },
    { ref: 'xlsx-clean/clean', skill: '엑셀 정리', summary: '빈 행과 합계 행을 정리합니다.' },
  ],
  copyKinds: [{ kind: 'claim', label: '자료에 없는 사실' }, { kind: 'medical', label: '효능' }, { kind: 'guarantee', label: '보장' }],
}

/** The built-in 빠진 정보 detectors (`gap_questions.detector_catalogue`). */
export const gapDetectors = [
  { name: 'meeting_date', label: '회의 날짜(연도까지)' },
  { name: 'attendees', label: '참석자' },
  { name: 'prices', label: '비용으로 고를 때 대안별 금액' },
]

/** The shipped 묶음, as `GET /agent-bases` sends them (`app/bundles/*.toml`). */
const targets = (report: string | null, chat: string | null) => ({ report, chat })
export const shippedBundles = [
  { ref: 'prepare', name: '쓰기 전에 준비하기', description: '빠진 정보를 먼저 묻고, 날짜·금액을 미리 계산해 둡니다.', shipped: true,
    ownerId: null, visibility: 'org', review: '', risky: false, items: [
      { id: 'ask', kind: 'check', ref: 'gap-check', settings: {}, label: '빠진 정보 먼저 묻기', default: true, targets: targets('check', 'check') },
      { id: 'dates', kind: 'skill', ref: 'date-calc', settings: {}, label: '날짜 미리 계산하기', default: true, targets: targets('flow', 'before') },
      { id: 'amounts', kind: 'skill', ref: 'quote-calc', settings: {}, label: '금액·합계 미리 계산하기', default: true, targets: targets('flow', 'before') },
    ] },
  { ref: 'review', name: '다 쓴 뒤 검토하기', description: '자료에 없는 값과 형식에 어긋난 곳을 찾아 고치고 다시 확인합니다.', shipped: true,
    ownerId: null, visibility: 'org', review: '', risky: false, items: [
      { id: 'ground', kind: 'check', ref: 'grounding-check', settings: {}, label: '자료에 없는 내용이 없는지 근거 확인하기', default: true, targets: targets('check', 'check') },
      { id: 'numbers', kind: 'option', ref: 'tool_numbers', settings: {}, label: '수치는 도구로 계산한 결과에서만 인정', default: false, targets: targets('option', 'option') },
      { id: 'titles', kind: 'option', ref: 'titles', settings: {}, label: '직함·제품명까지 근거 확인', default: false, targets: targets('option', 'option') },
      { id: 'format', kind: 'flow', ref: 'format-checks', settings: {}, label: '형식 검사', default: true, targets: targets('flow', null) },
      { id: 'repair', kind: 'flow', ref: 'repair', settings: {}, label: '어긋난 곳 고치고 다시 확인하기', default: true, targets: targets('flow', 'flow') },
    ] },
  { ref: 'finish', name: '마무리하기', description: '상투적인 표현을 걷어내고, 다 쓰면 파일로 내보냅니다.', shipped: true,
    ownerId: null, visibility: 'org', review: '', risky: false, items: [
      { id: 'plain', kind: 'check', ref: 'generic-language-killer', settings: {}, label: '상투적인 표현 걷어내기', default: true, targets: targets('check', 'check') },
      // A 공문-family document (개조식 included) goes out as HWPX, anything else as DOCX.
      { id: 'export', kind: 'export', ref: '', settings: { formats: ['docx'], by_format: { internal_draft: 'hwpx', official: 'hwpx', event_plan: 'hwpx', result_report: 'hwpx', gaejo: 'hwpx' } },
        label: '다 쓰면 파일로 내보내기', default: true, targets: targets('after', null) },
    ] },
]
const officialBundles = [{ bundle: 'prepare', items: {} }, { bundle: 'review', items: {} }, { bundle: 'finish', items: { export: false } }]
export const bases = [
  { key: 'gaejo-report', name: '개조식 보고', description: '긴 메모를 1쪽 개조식 요약 보고로', surface: 'report', docFormat: 'gaejo',
    template: 'doc-gaejo-report', steps: ['올린 자료를 읽습니다', '빠진 정보가 있으면 먼저 물어봅니다', '날짜·견적·인력 수치를 계산해 둡니다',
      '정해진 네 부분으로 개조식 보고를 씁니다', '개조식 문체와 한 쪽 분량을 확인합니다'], draft: false, custom: true,
    bundles: officialBundles, formatChecks: ['개조식 문체 확인', '날짜 확인'], asks: true, templateName: '개조식 보고서' },
  { key: 'minutes', name: '회의록', description: '회의·인터뷰·강의 기록', surface: 'report', docFormat: 'minutes', template: 'doc-minutes',
    steps: ['올린 녹음 기록·메모를 읽습니다', '회의 날짜처럼 빠진 정보가 있으면 먼저 물어봅니다',
      '「다음 주 금요일」 같은 날짜를 회의일 기준으로 계산해 둡니다', '회의록을 씁니다', '할 일마다 담당자·기한과 근거를 확인합니다'],
    draft: false, custom: true, bundles: officialBundles, formatChecks: ['할 일의 담당자·기한 확인', '날짜 확인'], asks: true,
    templateName: '회의록' },
  { key: 'data-analysis', name: '자료 추출·집계', description: '엑셀·CSV에서 열을 뽑아 집계', surface: 'chat', docFormat: null, template: null,
    steps: ['빠진 정보가 있으면 먼저 물어봅니다', '답의 표기를 다듬습니다', '답의 수치가 도구로 계산한 결과에 있는지 확인합니다'],
    draft: false, custom: true, asks: true, formatChecks: [],
    bundles: [{ bundle: 'prepare', items: { dates: false, amounts: false } }, { bundle: 'review', items: { numbers: true } }] },
]
export const myBundle = { ref: 'user:b1111111111111111111111111111111', id: 'b1111111111111111111111111111111', name: '두 번 검토하고 PDF로',
  description: '', shipped: false, ownerId: ME, visibility: 'private', review: '', risky: true, mine: true, reviewNote: '',
  stages: [{ id: 's1', kind: 'agent', ref: '@self', settings: {} }, { id: 's2', kind: 'export', ref: '', settings: { formats: ['pdf'] } }],
  items: [{ id: 's1', kind: 'agent', ref: '@self', settings: {}, label: '이 에이전트', default: true, targets: targets('self', 'self') },
    { id: 's2', kind: 'export', ref: '', settings: { formats: ['pdf'] }, label: '내보내기', default: true, targets: targets('after', null) }] }

/** The agent-draft `GET /sessions/{id}/agent-draft` sends for a conversation. */
export const agentDraft = {
  name: '주간 보고 에이전트', description: '', decided: ['보고 대상은 팀장. 결론을 맨 앞에 둔다.', '한 쪽을 넘기지 않는다.'],
  systemPrompt: '이 대화에서 정한 것:\n- 보고 대상은 팀장. 결론을 맨 앞에 둔다.\n- 한 쪽을 넘기지 않는다.',
  kinds: ['report'], surface: 'report', renderTemplateId: null, designSystemId: null, skillIds: [], mode: 'base', base: 'gaejo-report',
  bundles: officialBundles, files: [{ id: 'f1111111111111111111111111111111', name: '주간 메모.txt', size: 1200 }],
  fromAgentId: null, strictLocal: false,
}

export const myAgents = [
  agentRow('a1111111111111111111111111111111', '논문 리뷰어', {
    guide: '논문 PDF 를 첨부하고 학회 이름을 알려 주세요.', starters: ['첨부한 논문을 리뷰어 관점에서 검토해 줘'],
    kinds: ['chat', 'report'], runs: 12, visibility: 'org', baseKey: 'gaejo-report', bundles: officialBundles,
    process: { mode: 'base', base: 'gaejo-report', bundles: officialBundles, review: '', needsReview: false,
      chain: ['올린 자료를 읽습니다', '빠진 정보가 있으면 먼저 물어봅니다', '정해진 네 부분으로 개조식 보고를 씁니다'] },
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
/** A shipped skill kept only as a part (`listed: false`): 숨김 포함 shows it. */
export const hiddenSkillRow = skillRow('k8888888888888888888888888888888', '글자 수 맞춤', { source: 'built-in', catalogKey: 'text-fit', listed: false })
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

/** A person's template as `/design-templates` lists it beside the shipped ones. */
function asDesignTemplate(row: Record<string, unknown>) {
  return { id: row.id, kind: 'document', surface: 'report', name: row.name, nameEn: '', category: row.category || '내 템플릿',
    categoryEn: '', description: row.description ?? '', descriptionEn: '', fills: row.fills ?? [], fillsEn: [], checks: [],
    examplePrompt: '', examplePromptEn: '', formFormat: '', hasPreview: false, slideCount: 0, user: true, mine: true }
}

const designs = [
  { id: 'd1', name: '학과 공식 문서', description: '남색 강조', tokens: { accent: '#1f3a8a', ink: '#111111', muted: '#666666', font: 'serif' },
    body: '', imageStyle: '', craft: [], shared: false, mine: true, updatedAt: at },
  { id: 'd2', name: '기관 공용', description: '공용 디자인', tokens: { accent: '#0e8a5f', ink: '#111111', muted: '#666666', font: 'gothic' },
    body: '', imageStyle: '', craft: [], shared: true, mine: false, updatedAt: at },
]

/** Two catalogue servers of different categories; the list shows them flat, official first. */
export const connectorCatalog = [
  { slug: 'youtube', name: 'YouTube', description: '영상 자막과 정보를 읽습니다.', category: '미디어', transport: 'stdio', auth: 'none',
    kinds: ['chat', 'report', 'slides'], official: true, installed: false, requiredEnv: [] },
  { slug: 'arxiv', name: 'arXiv', description: '논문을 찾습니다.', category: '연구', transport: 'http', auth: 'none',
    kinds: ['chat'], official: false, installed: false, requiredEnv: [] },
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

export async function hubFixture(page: Page, testInfo: TestInfo, { emptyMine = false, conversations = 0, draft = agentDraft as Record<string, unknown>, admin = false, hiddenSkill = false, listDelayMs = 0, decided = undefined as { distilled: string; prompt: string; decided: string[]; delayMs?: number }[] | undefined } = {}) {
  // 「에이전트로 저장」's distillation replies, in order; by default the draft's own 지시.
  const decidedReplies = decided ? [...decided] : [{ distilled: 'ok', prompt: String(draft.systemPrompt ?? ''), decided: (draft.decided ?? []) as string[] }]
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const problems: string[] = []
  const calls: { method: string; path: string; body: Record<string, unknown> }[] = []
  const reads: string[] = []
  let made: ReturnType<typeof pipelineRow>[] = []
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  let agents = [...(emptyMine ? [] : myAgents), ...storeAgents]
  let skills = emptyMine ? [] : [...mySkills, ...(hiddenSkill ? [hiddenSkillRow] : [])]
  const store = [...storeSkills, ...(hiddenSkill ? [{ ...hiddenSkillRow, id: 'k9999999999999999999999999999999', ownerId: 'admin', ownerName: '관리자', official: true, installed: true, installs: 3, visibility: 'org' }] : [])]
    .map((s) => ({ ...s }))
  let sessions: Record<string, unknown>[] = conversationRows(conversations)
  let userTemplates: Record<string, unknown>[] = []
  let catalogRows: Record<string, unknown>[] = [
    { id: 'e1', slug: 'time', name: '시간', description: "현재 시각과 타임존 변환. '오늘'이 며칠인지 서버 시계로 알려 줍니다.", category: '기본',
      transport: 'stdio', endpoint: "uvx --with 'mcp<2' mcp-server-time --local-timezone=Asia/Seoul", env: {}, requiredEnv: [],
      toolsKeyVar: '', toolsKeyFeature: '', kinds: ['chat', 'report', 'slides'], enabled: true, listed: true, position: 0, updatedAt: at },
    { id: 'e2', slug: 'youtube', name: 'YouTube 전사', description: '영상의 자막을 가져오고, 없으면 음성을 전사합니다.', category: '미디어',
      transport: 'stdio', endpoint: 'uv run --script /srv/mcp/youtube.py', env: { WHISPER_URL: '${TOOLS_STT_URL}' }, requiredEnv: [],
      toolsKeyVar: 'WHISPER_URL', toolsKeyFeature: 'stt', kinds: ['chat', 'report'], enabled: true, listed: false, position: 1, updatedAt: at },
  ]

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
    if (method === 'GET') reads.push(path)
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })

    if (method === 'POST' && path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: ME, name: 'Fixture', email: 'fixture@example.test', role: admin ? 'admin' : 'user', status: 'active', monthlyCredits: 1000,
        creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
        preferences: { autoMemory: false, showUsage: false, streamResponses: true } } })
    if (method === 'GET' && path === '/auth/config') return json({ brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report', 'slides'], privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false })
    if (method === 'GET' && path === '/models') return json({ models: [{ id: 'fixture/base', label: 'Fixture', name: 'Fixture',
      vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report', 'slides'], modality: 'chat', dataBoundary: 'external',
      creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }], defaultChatModel: 'fixture/base',
      litellmAvailable: false, autoRouting: { enabled: false, available: false } })
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    // A slow server: the hub's lists answer late.
    if (listDelayMs && method === 'GET' && ['/agents', '/skills', '/skills/store'].includes(path)) {
      await new Promise((resolve) => setTimeout(resolve, listDelayMs))
    }
    if (method === 'GET' && path === '/agents') return json(agents)
    if (method === 'GET' && path === '/skills') return json(skills)
    if (method === 'GET' && path === '/skills/store') return json(store)
    if (method === 'GET' && path === '/tools') return json(tools)
    if (method === 'GET' && path === '/designs') return json(designs)
    if (method === 'GET' && path === '/design-templates') return json([...templates, ...userTemplates.map(asDesignTemplate)])
    // A person's templates (`/user-templates`): the editor's catalogue as the server makes it.
    if (method === 'GET' && path === '/user-templates/catalogue') return json(templateFixture.catalogue)
    if (method === 'GET' && path === '/user-templates') return json({ mine: userTemplates, shared: [] })
    if (method === 'GET' && path.startsWith('/user-templates/start/')) return json(templateFixture.startMinutes)
    if (method === 'POST' && path === '/user-templates') {
      const row = { ...body, id: 'ut-00000000-0000-0000-0000-000000000001', formName: '', hasForm: false, mine: true, ownerName: '', updatedAt: at }
      userTemplates = [row as Record<string, unknown>]
      return json(row, 201)
    }
    if (method === 'GET' && /^\/user-templates\/[^/]+$/.test(path)) {
      const row = userTemplates.find((r) => path.endsWith(String(r.id)))
      return row ? json(row) : json({ detail: 'template_not_found' }, 404)
    }
    if (method === 'PUT' && /^\/user-templates\/[^/]+$/.test(path)) {
      userTemplates = userTemplates.map((r) => (path.endsWith(String(r.id)) ? { ...r, ...body } : r))
      return json(userTemplates.find((r) => path.endsWith(String(r.id))))
    }
    if (method === 'GET' && path === '/admin/connector-catalog') return json({ entries: catalogRows, kinds: ['chat', 'report', 'slides'],
      transports: ['stdio', 'http', 'sse'], features: ['search', 'fetch', 'exec', 'research', 'stt', 'index'],
      placeholders: ['${TOOLS_RESEARCH_URL}', '${TOOLS_STT_URL}', '${TOOLS_SEARCH_URL}', '${TOOLS_FETCH_URL}', '${TOOLS_EXEC_URL}'] })
    if (method === 'POST' && path === '/admin/connector-catalog') {
      catalogRows = [...catalogRows, { ...(body as Record<string, unknown>), id: `e${catalogRows.length + 1}`, updatedAt: at,
        env: Object.fromEntries(Object.entries((body.env ?? {}) as Record<string, string>).map(([k, v]) => [k, v && v.startsWith('${') ? v : null])) }]
      return json({ entries: catalogRows }, 201)
    }
    if (method === 'GET' && path === '/admin/settings') return json(null, 404)
    if (method === 'GET' && path === '/user-pipelines') return json(emptyMine ? [] : myPipelines)
    if (method === 'GET' && path === '/user-pipelines/store') return json(storePipelines)
    if (method === 'GET' && path === '/user-pipelines/catalogue') return json({ maxSteps: 12, skills: [], checks: [], human: [
      { ref: 'gaps', name: '확인 질문', description: '' }, { ref: 'outline', name: '개요 확인', description: '' }],
      exportFormats: ['docx', 'pdf', 'hwpx', 'md'], reviewModels: [] })
    if (method === 'GET' && /^\/user-pipelines\/[^/]+$/.test(path)) {
      const row = [...myPipelines, ...made].find((p) => path.endsWith(p.id))
      return row ? json(row) : json({ detail: 'pipeline_not_found' }, 404)
    }
    if (method === 'GET' && /^\/agents\/[^/]+\/pipeline$/.test(path)) return json({ name: 'comparison-report', formats: [], agents: [],
      options: {}, checks: [], flows: { write: [{ name: 'draft', label: '초안', kind: 'model' }] } })
    if (method === 'GET' && /^\/agents\/[^/]+\/knowledge/.test(path)) return json([])
    if (method === 'GET' && path === '/agent-bases') return json({ bases, bundles: [...shippedBundles, ...(emptyMine ? [] : [myBundle])],
      exportFormats: ['docx', 'pdf', 'hwpx', 'md'], gapDetectors })
    if (method === 'GET' && path === '/skills/server-catalogue') return json(serverCatalogue)
    if (method === 'GET' && path === '/bundles') return json({ shipped: shippedBundles, mine: emptyMine ? [] : [myBundle], shared: [] })
    if (method === 'POST' && path === '/bundles') return json({ ...myBundle, ref: 'user:b2', id: 'b2', name: String(body.name) }, 201)
    if (method === 'POST' && /^\/bundles\/[^/]+\/share$/.test(path)) return json({ ...myBundle, review: 'pending' })
    if (method === 'POST' && path === '/user-pipelines/validate') return json({ ok: true, surface: 'report', problems: [], warnings: [], titles: [] })
    if (method === 'PUT' && /^\/agents\/[^/]+\/process$/.test(path)) {
      const id = path.split('/')[2]
      const process = { mode: body.mode, base: body.base ?? null, bundles: body.bundles ?? [], chain: ['바로 답합니다'], review: '',
        needsReview: false, warnings: [] }
      agents = agents.map((a) => (a.id === id ? { ...a, process, baseKey: body.base ?? null } : a))
      return json({ ...process, agent: {} })
    }
    if (method === 'GET' && /^\/sessions\/[^/]+\/agent-draft$/.test(path))
      return json(url.searchParams.get('distill') === 'false' ? { ...draft, systemPrompt: '', decided: [], distilled: 'pending' } : draft)
    if (method === 'GET' && /^\/sessions\/[^/]+\/agent-draft\/decided$/.test(path)) {
      const reply = decidedReplies.length > 1 ? decidedReplies.shift()! : decidedReplies[0]
      if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs))
      const { delayMs: _delay, ...rest } = reply
      return json(rest)
    }
    if (method === 'POST' && /^\/agents\/[^/]+\/knowledge\/from-files$/.test(path)) return json([], 201)
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
      // As the API checks it: a theme the person can neither own nor see is refused.
      if (body.designSystemId && !designs.some((d) => d.id === body.designSystemId)) {
        return json({ detail: 'design_system_not_found' }, 404)
      }
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
    if (method === 'GET' && path === '/connectors/catalog') return json(connectorCatalog)
    if (method === 'GET' && ['/projects', '/memory', '/connectors', '/jobs', '/shares', '/artifacts',
      '/history'].includes(path)) return json([])
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'not_found' }, 404)
  })
  return { unexpected, problems, calls, reads }
}
