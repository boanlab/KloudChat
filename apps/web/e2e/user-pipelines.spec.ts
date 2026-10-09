import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * Pipeline phase 3 screens against fixture data. Flows live on their own page (/pipelines): a
 * shipped flow copied into the person's own and edited as a list (add, reorder, settings, live
 * contract messages) with one action bar, saved, submitted for sharing, guarded against leaving
 * unsaved, bound to an agent from the agent editor's read-only 「워크플로」 summary and started; the
 * store's shared flows; the administrator's approval queue; a composed run's stages.
 */

test.use({ serviceWorkers: 'block' })

const at = '2026-10-08T07:00:00.000Z'
const composedSession = 'a4444444444444444444444444444444'
const shotsDir = process.env.PIPELINE_SHOTS ?? ''

function agentRow(id: string, name: string, surface: 'report' | 'chat', extra: Record<string, unknown> = {}) {
  return {
    ownerId: 'fixture-user', ownerName: 'Fixture', id, name, slug: id, description: `${name} 에이전트`, model: '',
    systemPrompt: '', tools: [], skillIds: [], kinds: [surface], surface, renderTemplateId: null, docFormat: null,
    designSystemId: null, guide: '', starters: [], shareMode: 'open', sealed: false, temperature: 0.3, color: '#168267',
    enabled: true, visibility: 'private', installs: 0, catalogKey: null, originId: null, official: false,
    installed: false, runs: 2, hasKnowledge: false, updatedAt: at, ...extra,
  }
}

const agents = [
  agentRow('c1111111111111111111111111111111', '데이터 분석', 'chat', { catalogKey: 'data-analysis' }),
  agentRow('c2222222222222222222222222222222', '비교 보고서', 'report', { catalogKey: 'comparison-report', docFormat: 'comparison' }),
  agentRow('c3333333333333333333333333333333', '개조식 보고', 'report', { catalogKey: 'gaejo-report', docFormat: 'gaejo' }),
]
const [dataAgent, cmpAgent, gaejoAgent] = agents

const shipped = {
  name: 'comparison-report', formats: ['comparison'], agents: ['comparison-report'], options: {}, checks: ['grounding-check', 'gap-check'],
  flows: {
    pre: [{ name: 'check:gaps', label: '빠진 정보 찾기', kind: 'check', checks: ['gap-check'] }],
    write: [
      { name: 'research', label: '자료 검색', kind: 'io' },
      { name: 'prepass', label: '사전 계산', kind: 'compute' },
      { name: 'draft', label: '초안', kind: 'model' },
      { name: 'check:grounding', label: '근거 확인', kind: 'check', checks: ['grounding-check'] },
      { name: 'verify', label: '검증', kind: 'check', checks: ['lead-dates'] },
    ],
  },
  summary: { material: '비교할 대안의 자료를 읽습니다', check: '비교표의 빈칸과 점수가 계산과 맞는지 확인합니다' },
  checkNames: { 'gap-check': '빠진 정보 묻기', 'grounding-check': '근거 대조', 'lead-dates': '날짜 확인' },
}

const catalogue = {
  maxSteps: 12,
  skills: [{ ref: 'date-calc', name: '날짜 계산', description: '', summary: '상대 날짜를 기준일로 풀고 기간을 셉니다.' },
    { ref: 'quote-calc', name: '견적 계산', description: '', summary: '견적 항목을 원 단위로 합치고 부가세·합계를 계산합니다.' }],
  checks: [{ ref: 'grounding-check', name: '근거 대조', description: '다 쓴 문서·답의 수치·날짜가 자료에 있는지 대조합니다.', needs: ['document', 'answer'] },
    { ref: 'copy-check', name: '카피 점검', description: '광고·홍보 문구를 점검합니다.', needs: ['answer'] }],
  human: [{ ref: 'gaps', name: '확인 질문', description: '쓰기 전에 자료에 없는 필수 정보를 한 번에 묻습니다.' },
    { ref: 'outline', name: '개요 확인', description: '뒤 에이전트가 잡은 개요를 확인받고 씁니다.' }],
  exportFormats: ['docx', 'pdf', 'hwpx', 'md'],
  reviewModels: [{ id: 'fixture/base', label: 'Fixture' }, { id: 'fixture/judge', label: 'Fixture Judge' }],
}

type Step = { id: string; kind: string; ref: string; settings: Record<string, unknown>; title?: string }

const KIND = { agent: '에이전트', skill: '스킬', check: '검사', review: '검토', human: '사람 확인', export: '내보내기' } as Record<string, string>

function title(step: Step) {
  if (step.kind === 'agent') return agents.find((a) => a.id === step.ref)?.name ?? '에이전트'
  if (step.kind === 'check') return catalogue.checks.find((c) => c.ref === step.ref)?.name ?? step.ref
  if (step.kind === 'skill') return catalogue.skills.find((c) => c.ref === step.ref)?.name ?? step.ref
  if (step.kind === 'human') return catalogue.human.find((c) => c.ref === step.ref)?.name ?? step.ref
  return step.kind === 'review' ? '검토' : '내보내기'
}

/** The server's contract check, as far as these screens need it. */
function verdict(steps: Step[]) {
  const problems: { step: number; code: string; message: string }[] = []
  let product = ''
  steps.forEach((step, i) => {
    const at = `${i + 1}번 ${KIND[step.kind]}(${title(step)})`
    if (step.kind === 'agent') product = agents.find((a) => a.id === step.ref)?.surface === 'report' ? 'document' : 'answer'
    if ((step.kind === 'check' || step.kind === 'review') && !product) {
      problems.push({ step: i + 1, code: 'needs_product', message: `${at}: 앞 단계에서 만든 문서나 답이 있어야 합니다.` })
    }
    if (step.kind === 'export' && product !== 'document') {
      problems.push({ step: i + 1, code: 'needs_product', message: `${at}: 앞 단계에서 만든 문서가 있어야 합니다.` })
    }
  })
  return { ok: problems.length === 0, surface: product === 'document' ? 'report' : product ? 'chat' : '', problems, warnings: [] }
}

function pipelineRow(id: string, name: string, stages: Step[], extra: Record<string, unknown> = {}) {
  return {
    id, name, description: '', base: 'comparison-report', originId: null, visibility: 'private', review: '', reviewNote: '',
    reviewedAt: null, version: 1, installs: 0, ownerId: 'fixture-user', ownerName: 'Fixture', boundAgents: [],
    createdAt: at, updatedAt: at, ...verdict(stages), stages: stages.map((s) => ({ ...s, title: title(s) })), ...extra,
  }
}

function sse(events: unknown[]) {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
}

function composedRun() {
  const runId = 'rc0000000000000000000000000000001'
  const rec = (name: string, label: string, kind: string, summary: string, status = 'done') => ({
    id: `${runId}:${name}:1`, runId, name, label, kind, status, reason: '', summary, startedAt: at, finishedAt: at,
    ms: 900, attempt: 1, rerunnable: true,
  })
  return { id: runId, pipeline: 'user:p1', flow: 'compose', status: 'done', parentId: null, fromStage: null, stages: [
    rec('1:agent', '데이터 분석', 'model', '답 412자'),
    rec('2:agent', '비교 보고서', 'model', '절 5개 작성'),
    rec('3:check', '근거 대조', 'check', '근거 없는 값 1개 → 빈칸'),
    rec('4:agent', '개조식 보고', 'model', '절 4개 작성'),
    rec('5:review', '검토', 'check', '지적 2개 · 고침 1개'),
    rec('save', '저장', 'io', ''),
  ] }
}

async function fixture(page: Page, testInfo: TestInfo, { admin = false } = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const problems: string[] = []
  const calls: string[] = []
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  let mine: ReturnType<typeof pipelineRow>[] = []
  let pending = [pipelineRow('p9', '회의록 → 개조식 1쪽', [
    { id: 's1', kind: 'human', ref: 'gaps', settings: {} },
    { id: 's2', kind: 'agent', ref: cmpAgent.id, settings: {} },
    { id: 's3', kind: 'review', ref: '', settings: { model: 'fixture/judge', repair: true } },
  ], { review: 'pending', ownerId: 'u2', ownerName: '김동료' })]
  const store = [pipelineRow('p7', '분석 → 비교 → 개조식 요약', [
    { id: 's1', kind: 'agent', ref: dataAgent.id, settings: {} },
    { id: 's2', kind: 'agent', ref: cmpAgent.id, settings: {} },
    { id: 's3', kind: 'check', ref: 'grounding-check', settings: {} },
    { id: 's4', kind: 'agent', ref: gaejoAgent.id, settings: {} },
    { id: 's5', kind: 'export', ref: '', settings: { formats: ['hwpx'] } },
  ], { review: 'approved', visibility: 'org', ownerId: 'u2', ownerName: '김동료', installs: 3, mine: false, installed: false,
    description: '데이터를 분석해 비교 보고서를 쓰고, 1쪽 개조식 요약까지 만듭니다.' })]
  const composed = {
    id: composedSession, kind: 'report', title: '사업자 견적 비교', projectId: null, agentId: null, model: 'fixture/base',
    routingMode: 'manual', artifactId: null, pending: null, skillIds: [], renderTemplateId: null, pinned: false,
    createdAt: at, updatedAt: at, preview: null, messageCount: 2, made: null,
    messages: [
      { id: 'u1', role: 'user', content: '첨부한 두 견적을 비교해 1쪽 개조식으로 정리해 줘.', steps: null, attachments: null, variants: null,
        usage: null, model: null, routing: null, startedFrom: null, rating: null, artifactIds: null, failure: null, createdAt: at },
      { id: 'u2', role: 'assistant', content: '4개 섹션으로 보고서를 작성했습니다.', steps: null, attachments: null, variants: null,
        usage: null, model: 'fixture/base', routing: { run: composedRun() }, startedFrom: null, rating: null, artifactIds: null,
        failure: null, createdAt: at },
    ],
  }
  let started = false

  await page.context().routeWebSocket(/^(?!wss?:\/\/[^/]+\/\?token=).*$/, (socket) => { unexpected.push(`WebSocket ${socket.url()}`); socket.close() })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method !== 'GET') calls.push(`${method} ${path}`)
    const json = () => (request.postDataJSON() ?? {}) as Record<string, unknown>
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: 'Pipeline fixture', email: 'fixture@example.test', role: admin ? 'admin' : 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
        createdAt: at, preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report', 'slides'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'fixture/base', label: 'Fixture',
      name: 'Fixture', vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report', 'slides'], modality: 'chat',
      dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/base', litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: started ? [{ ...composed, messages: null }] : [] })
    if (method === 'GET' && path === `/sessions/${composedSession}`) return route.fulfill({ json: composed })
    if (method === 'GET' && path === '/agents') return route.fulfill({ json: agents })
    if (method === 'GET' && /^\/agents\/[^/]+\/pipeline$/.test(path)) return route.fulfill({ json: shipped })
    if (method === 'GET' && /^\/agents\/[^/]+\/knowledge/.test(path)) return route.fulfill({ json: [] })
    // user pipelines
    if (method === 'GET' && path === '/user-pipelines') return route.fulfill({ json: mine })
    if (method === 'GET' && path === '/user-pipelines/store') return route.fulfill({ json: store })
    if (method === 'GET' && path === '/user-pipelines/catalogue') return route.fulfill({ json: catalogue })
    if (method === 'POST' && path === '/user-pipelines/validate') {
      const stages = json().stages as Step[]
      return route.fulfill({ json: { ...verdict(stages), titles: stages.map(title) } })
    }
    if (method === 'POST' && path === '/user-pipelines') {
      const body = json()
      if (body.fromId) {
        store[0] = { ...store[0], installed: true, installs: store[0].installs + 1 }
        const copy = pipelineRow('p8', store[0].name, store[0].stages as Step[], { originId: 'p7' })
        mine = [...mine, copy]
        return route.fulfill({ status: 201, json: copy })
      }
      const made = pipelineRow('p1', '비교 보고서 워크플로', [
        { id: 's1', kind: 'human', ref: 'gaps', settings: {} },
        { id: 's2', kind: 'agent', ref: cmpAgent.id, settings: { catalogKey: 'comparison-report' } },
      ])
      mine = [...mine, made]
      return route.fulfill({ status: 201, json: made })
    }
    const own = mine.find((p) => path.startsWith(`/user-pipelines/${p.id}`))
    if (own && method === 'PUT') {
      const body = json() as { name: string; stages: Step[] }
      const next = pipelineRow(own.id, body.name, body.stages, { version: own.version + 1, boundAgents: own.boundAgents })
      mine = mine.map((p) => (p.id === own.id ? next : p))
      return route.fulfill({ json: next })
    }
    if (own && method === 'POST' && path.endsWith('/submit')) {
      const next = { ...own, review: 'pending' }
      mine = mine.map((p) => (p.id === own.id ? next : p))
      return route.fulfill({ json: next })
    }
    if (method === 'POST' && path.endsWith('/start')) {
      started = true
      return route.fulfill({ status: 201, json: { sessionId: composedSession, kind: 'report', pipelineId: 'p1' } })
    }
    if (method === 'GET' && /^\/user-pipelines\/[^/]+$/.test(path)) {
      const row = mine.find((p) => path === `/user-pipelines/${p.id}`)
      return row ? route.fulfill({ json: row }) : route.fulfill({ status: 404, json: { detail: 'pipeline_not_found' } })
    }
    if (method === 'PATCH' && /^\/agents\/[^/]+$/.test(path)) {
      const row = agents.find((a) => path === `/agents/${a.id}`)
      return route.fulfill({ json: { ...row, ...json() } })
    }
    if (method === 'PUT' && /^\/agents\/[^/]+\/user-pipeline$/.test(path)) {
      const agentId = path.split('/')[2]
      const { pipelineId } = json() as { pipelineId: string | null }
      const agentName = agents.find((a) => a.id === agentId)?.name ?? ''
      mine = mine.map((p) => ({ ...p, boundAgents: p.id === pipelineId ? [{ id: agentId, name: agentName }]
        : p.boundAgents.filter((b) => b.id !== agentId) }))
      return route.fulfill({ json: { agentId, pipelineId } })
    }
    if (method === 'GET' && path === '/admin/user-pipelines') return route.fulfill({ json: pending })
    if (method === 'POST' && /^\/admin\/user-pipelines\/[^/]+\/(approve|reject)$/.test(path)) {
      const id = path.split('/')[3]
      pending = pending.filter((p) => p.id !== id)
      return route.fulfill({ json: { id } })
    }
    if (method === 'GET' && path.startsWith('/admin/')) return route.fulfill({ json: path === '/admin/users' ? [] : {} })
    if (method === 'GET' && (path.startsWith('/sessions/') && (path.endsWith('/messages') || path.endsWith('/jobs')))) return route.fulfill({ json: [] })
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/projects', '/skills', '/memory', '/tools', '/connectors', '/connectors/catalog',
      '/jobs', '/designs', '/shares', '/design-templates', '/skills/store', '/artifacts'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  return { unexpected, problems, calls, mine: () => mine }
}

function shooter(page: Page, testInfo: TestInfo) {
  const dir = shotsDir ? `${shotsDir}/${testInfo.project.name}` : testInfo.outputPath('shots')
  let n = 0
  return async (name: string, full = false) => {
    n += 1
    await page.screenshot({ path: `${dir}/${testInfo.title.split(':')[0]}-${String(n).padStart(2, '0')}-${name}.png`, fullPage: full })
  }
}

async function addStep(page: Page, group: string, name: string) {
  await page.getByRole('button', { name: '단계 추가' }).click()
  const picker = page.getByRole('dialog', { name: '단계 추가' })
  await picker.getByRole('region', { name: group }).getByRole('button', { name: new RegExp(`^${name}`) }).click()
  await expect(picker).toBeHidden()
}

async function openAgentFlow(page: Page, name: string) {
  const agent = agents.find((a) => a.name === name)!
  await page.goto(`/customize/agents/${agent.id}`)
  // The agent's settings sit on its own screen; the flow is one folded group of them.
  const settings = page.getByRole('region', { name: '설정' })
  await expect(settings).toBeVisible({ timeout: 15_000 })
  await settings.getByText('워크플로 연결', { exact: true }).click()
  await expect(settings.getByTestId('agent-flow-summary')).toBeVisible()
  return settings
}

test('editor: copy the shipped flow, edit, validate, save, share, bind, start', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)

  // The agent editor shows the flow read-only, with only the agent's own actions.
  let dialog = await openAgentFlow(page, '비교 보고서')
  const summary = dialog.getByTestId('agent-flow-summary')
  await expect(summary.locator('p', { hasText: /^기본 워크플로$/ })).toBeVisible()
  await expect(summary.getByLabel(/워크플로:/)).toBeVisible()
  await expect(dialog.getByTestId('pipeline-editor')).toHaveCount(0)
  for (const gone of ['워크플로 저장', '공유 요청', '이 워크플로로 시작', '워크플로 삭제']) {
    await expect(dialog.getByRole('button', { name: gone })).toHaveCount(0)
  }
  await expect(dialog.getByRole('button', { name: '저장', exact: true })).toBeVisible()
  await shot('agent-summary-default')
  await summary.getByRole('link', { name: '워크플로 편집' }).click()

  // 「워크플로 편집 →」 lands on the shipped flows, this agent's card picked out.
  await expect(page).toHaveURL(new RegExp(`/customize\\?tab=workflows&view=mine&focus=${cmpAgent.id}`))
  const shippedCard = page.locator(`[data-agent="${cmpAgent.id}"]`)
  await expect(shippedCard).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('shipped-pipeline')).toHaveCount(3)
  // A few plain steps, each saying what happens; no stage ids, kinds or check ids until asked.
  const plain = shippedCard.getByTestId('workflow-step')
  expect(await plain.count()).toBeLessThanOrEqual(6)
  await expect(plain.first()).toContainText('자료 · 비교할 대안의 자료를 읽습니다')
  await expect(shippedCard).toContainText('기본 제공 · 복제해서 고칠 수 있어요')
  for (const internal of ['check:gaps', 'gap-check', 'prepass', '입출력', '모델', '사전 계산']) {
    await expect(shippedCard.getByText(internal, { exact: false })).toHaveCount(0)
  }
  await shot('shipped-locked')
  await shippedCard.getByRole('button', { name: '자세히 보기 (단계 6개)' }).click()
  const details = shippedCard.getByTestId('workflow-details')
  await expect(details.getByText('사전 계산')).toBeVisible()
  await expect(details.getByText('· 빠진 정보 묻기')).toBeVisible()
  await expect(details.getByText('· 날짜 확인')).toBeVisible()
  // Ids only as 기술 정보.
  await expect(details.getByTestId('workflow-tech')).toContainText('gap-check')
  await shot('shipped-details')
  await shippedCard.getByRole('button', { name: '복제해서 고치기' }).click()

  await expect(page).toHaveURL(/\/customize\/workflows\/p1$/)
  const editor = page.getByTestId('pipeline-editor')
  const actions = page.getByTestId('pipeline-actions')
  await expect(editor).toBeVisible()
  await expect(editor.getByTestId('pipeline-step')).toHaveCount(2)
  await shot('copied')

  // A check put first breaks its contract; the message names the step.
  await addStep(page, '검사', '근거 대조')
  const steps = editor.getByTestId('pipeline-step')
  await steps.nth(2).getByRole('button', { name: '위로' }).click()
  await steps.nth(1).getByRole('button', { name: '위로' }).click()
  await expect(editor.getByText('1번 검사(근거 대조): 앞 단계에서 만든 문서나 답이 있어야 합니다.')).toBeVisible()
  await expect(actions.getByRole('button', { name: '저장', exact: true })).toBeDisabled()
  await shot('contract-message')
  await steps.nth(0).getByRole('button', { name: '아래로' }).click()
  await steps.nth(1).getByRole('button', { name: '아래로' }).click()
  await expect(editor.getByText('앞 단계에서 만든 문서나 답이 있어야 합니다.', { exact: false })).toHaveCount(0)

  // 데이터 분석 first, 개조식 1쪽 요약 after, a review on another model, a file.
  await addStep(page, '에이전트', '데이터 분석')
  await steps.nth(3).getByRole('button', { name: '위로' }).click()
  await steps.nth(2).getByRole('button', { name: '위로' }).click()
  await steps.nth(1).getByRole('button', { name: '위로' }).click()
  await addStep(page, '에이전트', '개조식 보고')
  await steps.nth(4).getByRole('button', { name: '설정' }).click()
  await steps.nth(4).getByRole('textbox').fill('1쪽 개조식으로 요약')
  await addStep(page, '검토', '검토')
  await steps.nth(5).getByRole('button', { name: '설정' }).click()
  const review = steps.nth(5).getByTestId('step-settings')
  await review.getByRole('combobox').selectOption('fixture/judge')
  await review.getByRole('switch', { name: '지적한 곳을 한 번 고치기' }).click()
  await addStep(page, '내보내기', '내보내기')
  await expect(steps).toHaveCount(7)
  await editor.getByRole('textbox', { name: '워크플로 이름' }).fill('분석 → 비교 → 개조식 요약')
  await steps.nth(5).scrollIntoViewIfNeeded()
  // One action bar, pinned to the bottom while the steps scroll.
  await expect(actions).toBeInViewport()
  await expect(page.getByRole('button', { name: '저장', exact: true })).toHaveCount(1)
  await expect(page.getByRole('button', { name: '삭제', exact: true })).toHaveCount(1)
  await shot('edited')

  // Leaving with unsaved edits asks first; staying keeps them.
  await page.getByRole('link', { name: '워크플로 목록' }).click()
  const leave = page.getByRole('dialog', { name: '저장하지 않고 나갈까요?' })
  await expect(leave).toBeVisible()
  await shot('leave-guard')
  await leave.getByRole('button', { name: '취소' }).click()
  await expect(page).toHaveURL(/\/customize\/workflows\/p1$/)
  await expect(steps).toHaveCount(7)

  await actions.getByRole('button', { name: '저장', exact: true }).click()
  await expect(actions.getByRole('button', { name: '저장', exact: true })).toBeDisabled()
  // The top bar names the flow as saved, not as it was opened.
  await expect(page.locator('header').getByText('분석 → 비교 → 개조식 요약', { exact: true })).toBeVisible()
  const saved = state.mine()[0]
  expect(saved.stages.map((s) => s.kind)).toEqual(['agent', 'human', 'agent', 'check', 'agent', 'review', 'export'])
  expect(saved.stages[5].settings).toMatchObject({ model: 'fixture/judge', repair: true })
  expect(saved.stages[4].settings).toMatchObject({ instruction: '1쪽 개조식으로 요약' })

  await actions.getByRole('button', { name: '공유 요청' }).click()
  await expect(editor.getByText('승인 대기')).toBeVisible()
  await shot('submitted')

  // Saved, so the list link leaves without asking.
  await page.getByRole('link', { name: '워크플로 목록' }).click()
  await expect(page).toHaveURL(/\/customize\?tab=workflows&view=mine$/)
  await expect(page.getByTestId('item-card')).toHaveCount(1)
  await shot('my-flows')

  // Bind it from the agent editor; the agent's own 「저장」 applies it.
  dialog = await openAgentFlow(page, '비교 보고서')
  await dialog.getByRole('combobox', { name: '연결할 워크플로' }).selectOption({ label: '분석 → 비교 → 개조식 요약' })
  await expect(dialog.getByText('저장하면 이 워크플로로 바뀝니다.')).toBeVisible()
  await expect(dialog.getByTestId('step-chips')).toContainText('개조식 보고')
  await shot('agent-summary-picked')
  expect(state.calls).not.toContain(`PUT /agents/${cmpAgent.id}/user-pipeline`)
  await dialog.getByRole('button', { name: '저장', exact: true }).click()
  await expect(dialog.getByText('저장했습니다.')).toBeVisible()
  expect(state.calls).toContain(`PUT /agents/${cmpAgent.id}/user-pipeline`)
  dialog = await openAgentFlow(page, '비교 보고서')
  await expect(dialog.getByRole('combobox', { name: '연결할 워크플로' })).toHaveValue('p1')
  await dialog.getByRole('link', { name: '워크플로 편집' }).click()
  await expect(page).toHaveURL(/\/customize\/workflows\/p1$/)
  await expect(page.getByText('연결된 에이전트: 비교 보고서')).toBeVisible()

  await page.getByTestId('pipeline-actions').getByRole('button', { name: '이 워크플로로 시작' }).click()
  await expect(page).toHaveURL(new RegExp(`/s/${composedSession}`))
  await expect(page.getByText('4개 섹션으로 보고서를 작성했습니다.')).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: /보고서를 썼습니다/ }).first().click()
  await expect(page.getByTestId('stage-list').first()).toBeVisible()
  await shot('composed-run')
  expect(state.calls).toEqual(expect.arrayContaining(['POST /user-pipelines', 'PUT /user-pipelines/p1',
    'POST /user-pipelines/p1/submit', `PUT /agents/${cmpAgent.id}/user-pipeline`, 'POST /user-pipelines/p1/start']))
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('store: shared flows to import or start', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)
  // Nothing of the person's own yet: the workflows tab opens on 탐색.
  await page.goto('/pipelines?tab=store')
  await expect(page).toHaveURL(/\/customize\?tab=workflows&view=explore/)
  const card = page.getByTestId('item-card').filter({ hasText: '분석 → 비교 → 개조식 요약' }).first()
  await expect(card).toBeVisible({ timeout: 15_000 })
  await shot('store')
  await card.getByRole('button', { name: '분석 → 비교 → 개조식 요약 추가' }).click()
  await expect.poll(() => state.mine().map((p) => p.originId)).toEqual(['p7'])
  // 내 항목 shows the imported flow without a reload.
  await page.getByRole('button', { name: '내 항목' }).click()
  await expect(page.getByTestId('item-card')).toHaveCount(1)
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('admin: approve or reject a pipeline asking to be shared', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo, { admin: true })
  const shot = shooter(page, testInfo)
  await page.goto('/admin/users')
  const card = page.getByTestId('pipeline-approval').first()
  await expect(card).toBeVisible({ timeout: 15_000 })
  await card.scrollIntoViewIfNeeded()
  await shot('approvals')
  await card.getByRole('button', { name: '반려' }).click()
  const dialog = page.getByRole('dialog', { name: '반려 사유' })
  await expect(dialog.getByRole('button', { name: '반려' })).toBeDisabled()
  await dialog.getByRole('textbox').fill('검토 모델 비용이 커서 기본 모델로 바꿔 주세요.')
  await shot('reject-reason')
  await dialog.getByRole('button', { name: '반려' }).click()
  await expect(page.getByText('승인을 기다리는 워크플로가 없습니다')).toBeVisible()
  expect(state.calls).toContain('POST /admin/user-pipelines/p9/reject')
  expect(state.problems).toEqual([])
})
