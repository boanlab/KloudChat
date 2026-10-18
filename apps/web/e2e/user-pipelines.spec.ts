import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'
import { viewport } from './project'
import type { UserPipeline } from '../src/types'

/**
 * An agent's 직접 구성 (고급) against fixture data: in the agent's own settings, the steps start from
 * the agent itself and are edited as a list (add, reorder, settings, live contract messages), then
 * saved with the agent; an order made before can be brought in; the administrator's 「진행 과정
 * 검토」 queue; a composed run's stages.
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
const [dataAgent, cmpAgent] = agents

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
  if (step.kind === 'agent' && step.ref === '@self') return cmpAgent.name
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
    if (step.kind === 'agent') {
      const surface = step.ref === '@self' ? 'report' : agents.find((a) => a.id === step.ref)?.surface
      product = surface === 'report' ? 'document' : 'answer'
    }
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
  // Typed so a store row's `installed`·`mine` and a later binding fit the same row.
  const store: Pick<UserPipeline, 'boundAgents' | 'installed' | 'mine'> = { boundAgents: [] }
  return {
    id, name, description: '', base: 'comparison-report', originId: null, visibility: 'private', review: '', reviewNote: '',
    reviewedAt: null, version: 1, installs: 0, ownerId: 'fixture-user', ownerName: 'Fixture', ...store,
    createdAt: at, updatedAt: at, ...verdict(stages), stages: stages.map((s) => ({ ...s, title: title(s) })), ...extra,
  }
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
  let pending = [pipelineRow('p9', '회의록 → 개조식 1쪽', [
    { id: 's1', kind: 'human', ref: 'gaps', settings: {} },
    { id: 's2', kind: 'agent', ref: cmpAgent.id, settings: {} },
    { id: 's3', kind: 'review', ref: '', settings: { model: 'fixture/judge', repair: true } },
  ], { review: 'pending', ownerId: 'u2', ownerName: '김동료' })]
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
  const processes: Record<string, unknown>[] = []
  const mine = [pipelineRow('p5', '예전에 만든 분석 → 비교', [
    { id: 's1', kind: 'agent', ref: dataAgent.id, settings: {} },
    { id: 's2', kind: 'agent', ref: cmpAgent.id, settings: {} },
  ])]

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
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [] })
    if (method === 'GET' && path === `/sessions/${composedSession}`) return route.fulfill({ json: composed })
    if (method === 'GET' && path === '/agents') return route.fulfill({ json: agents })
    if (method === 'GET' && /^\/agents\/[^/]+\/pipeline$/.test(path)) return route.fulfill({ json: shipped })
    if (method === 'GET' && /^\/agents\/[^/]+\/knowledge/.test(path)) return route.fulfill({ json: [] })
    // The 진행 과정 설정 editor's names; this spec does not open it.
    if (method === 'GET' && path === '/user-templates/catalogue') return route.fulfill({ status: 503, json: { detail: 'not_in_this_fixture' } })
    if (method === 'GET' && path === '/agent-bases') return route.fulfill({ json: { bases: [
      { key: 'comparison-report', name: '비교·결정', description: '', surface: 'report', docFormat: 'comparison',
        template: null, steps: ['비교할 대안의 자료를 읽습니다'], draft: false, custom: true, bundles: [], formatChecks: [], asks: true },
    ], bundles: [], exportFormats: ['docx', 'pdf', 'hwpx', 'md'] } })
    if (method === 'PUT' && /^\/agents\/[^/]+\/process$/.test(path)) {
      const body = json()
      processes.push(body)
      return route.fulfill({ json: { mode: body.mode, base: body.base ?? null, bundles: [], chain: [], review: '',
        needsReview: true, warnings: [], agent: {} } })
    }
    // user pipelines
    if (method === 'GET' && path === '/user-pipelines') return route.fulfill({ json: mine })
    if (method === 'GET' && path === '/user-pipelines/catalogue') return route.fulfill({ json: catalogue })
    if (method === 'POST' && path === '/user-pipelines/validate') {
      const stages = json().stages as Step[]
      return route.fulfill({ json: { ...verdict(stages), titles: stages.map(title) } })
    }
    if (method === 'PATCH' && /^\/agents\/[^/]+$/.test(path)) {
      const row = agents.find((a) => path === `/agents/${a.id}`)
      return route.fulfill({ json: { ...row, ...json() } })
    }
    if (method === 'GET' && path === '/admin/user-pipelines') return route.fulfill({ json: pending })
    if (method === 'GET' && path === '/admin/skills') return route.fulfill({ json: [] })
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
  return { unexpected, problems, calls, processes }
}

function shooter(page: Page, testInfo: TestInfo) {
  const dir = shotsDir ? `${shotsDir}/${viewport(testInfo)}` : testInfo.outputPath('shots')
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

async function openCustom(page: Page) {
  await page.goto(`/customize/agents/${cmpAgent.id}`)
  const settings = page.getByRole('region', { name: '설정' })
  await expect(settings).toBeVisible({ timeout: 15_000 })
  await settings.getByRole('radio', { name: /직접 구성/ }).check()
  const editor = settings.getByTestId('process-custom')
  await expect(editor).toBeVisible()
  return { settings, editor }
}

test('직접 구성: start from the agent itself, keep the contracts, save it with the agent', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)
  const { settings, editor } = await openCustom(page)
  // It starts from the agent's own step, its outline offered first as the report flow does.
  const steps = editor.getByTestId('pipeline-step')
  await expect(steps).toHaveText([/개요 확인/, /비교 보고서/])
  await expect(settings.getByText(/워크플로|작동 단계/)).toHaveCount(0)
  await shot('custom-start')

  // A check put first breaks its contract; the message names the step.
  await addStep(page, '검사', '근거 대조')
  await steps.nth(2).getByRole('button', { name: '위로' }).click()
  await steps.nth(1).getByRole('button', { name: '위로' }).click()
  await expect(editor.getByText('1번 검사(근거 대조): 앞 단계에서 만든 문서나 답이 있어야 합니다.')).toBeVisible()
  await shot('contract-message')
  await steps.nth(0).getByRole('button', { name: '아래로' }).click()
  await steps.nth(1).getByRole('button', { name: '아래로' }).click()
  await expect(editor.getByText('앞 단계에서 만든 문서나 답이 있어야 합니다.', { exact: false })).toHaveCount(0)

  // 데이터 분석 first, a review on another model, a file at the end.
  await addStep(page, '에이전트', '데이터 분석')
  await steps.nth(3).getByRole('button', { name: '위로' }).click()
  await steps.nth(2).getByRole('button', { name: '위로' }).click()
  await steps.nth(1).getByRole('button', { name: '위로' }).click()
  await addStep(page, '검토', '검토')
  await steps.nth(4).getByRole('button', { name: '설정' }).click()
  const review = steps.nth(4).getByTestId('step-settings')
  await review.getByRole('combobox').selectOption('fixture/judge')
  await review.getByRole('switch', { name: '지적한 곳을 한 번 고치기' }).click()
  await addStep(page, '내보내기', '내보내기')
  await expect(steps).toHaveCount(6)
  await shot('edited')

  await settings.getByRole('button', { name: '저장', exact: true }).click()
  await expect(settings.getByText('저장했습니다.')).toBeVisible()
  const saved = state.processes.at(-1) as { mode: string; stages: Step[] }
  expect(saved.mode).toBe('custom')
  expect(saved.stages.map((s) => `${s.kind}:${s.ref}`)).toEqual([
    'agent:c1111111111111111111111111111111', 'human:outline', 'agent:@self', 'check:grounding-check', 'review:', 'export:'])
  expect(saved.stages[4].settings).toMatchObject({ model: 'fixture/judge', repair: true })

  // A composed run keeps showing its stages.
  await page.goto(`/s/${composedSession}`)
  await expect(page.getByText('4개 섹션으로 보고서를 작성했습니다.')).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: /보고서를 썼습니다/ }).first().click()
  await expect(page.getByTestId('stage-list').first()).toBeVisible()
  await shot('composed-run')
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('직접 구성 can start from an order made before', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const { editor } = await openCustom(page)
  await editor.getByRole('combobox', { name: '저장한 순서 가져오기' }).selectOption({ label: '예전에 만든 분석 → 비교' })
  await expect(editor.getByTestId('pipeline-step')).toHaveText([/데이터 분석/, /비교 보고서/])
  expect(state.problems).toEqual([])
})

test('admin: 진행 과정 검토 approves or rejects what asks to be shared', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo, { admin: true })
  const shot = shooter(page, testInfo)
  await page.goto('/admin/users')
  await expect(page.getByRole('heading', { name: '진행 과정 검토' })).toBeVisible({ timeout: 15_000 })
  const card = page.getByTestId('pipeline-approval').first()
  await expect(card).toBeVisible({ timeout: 15_000 })
  await expect(card).toContainText('저장한 순서')
  await card.scrollIntoViewIfNeeded()
  await shot('approvals')
  await card.getByRole('button', { name: '반려' }).click()
  const dialog = page.getByRole('dialog', { name: '반려 사유' })
  await expect(dialog.getByRole('button', { name: '반려' })).toBeDisabled()
  await dialog.getByRole('textbox').fill('검토 모델 비용이 커서 기본 모델로 바꿔 주세요.')
  await shot('reject-reason')
  await dialog.getByRole('button', { name: '반려' }).click()
  await expect(page.getByText('검토를 기다리는 진행 과정이 없습니다')).toBeVisible()
  expect(state.calls).toContain('POST /admin/user-pipelines/p9/reject')
  expect(state.problems).toEqual([])
})
