import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { viewport } from './project'
import { shellRead } from './shell-mocks'

/**
 * The pipeline canvas of an agent's 직접 구성 against fixture data: steps placed from the palette
 * (click and drag), lines drawn from the side panel, problems on the node and in the banner, an
 * agent's skills 항상 적용 / 필요할 때, a condition step with a rule and branches, the same steps in
 * 목록으로 보기, and 실행 기록 with each step's status, input and output.
 */

test.use({ serviceWorkers: 'block' })

const at = '2026-10-20T05:00:00.000Z'
const shotsDir = process.env.CHAIN_SHOTS ?? ''
const ME = 'fixture-user'
const CHAIN = 'c9999999999999999999999999999999'
const MINUTES = 'c1111111111111111111111111111111'
const MAIL = 'c2222222222222222222222222222222'
const MADE = 'c3333333333333333333333333333333'

type Step = { id: string; kind: string; ref: string; settings?: Record<string, unknown>; name?: string; branches?: { key: string }[] }
type Edge = { from: string; to: string; branch?: string }

function agentRow(id: string, name: string, surface: 'report' | 'chat', extra: Record<string, unknown> = {}) {
  return {
    ownerId: ME, ownerName: 'Fixture', id, name, slug: id, description: `${name} 에이전트`, model: '', systemPrompt: '',
    tools: null, skillIds: [], activeSkillIds: [], kinds: [surface], surface, renderTemplateId: null, docFormat: null,
    designSystemId: null, guide: '', starters: [], shareMode: 'open', sealed: false, temperature: 0.3, color: '#168267',
    enabled: true, visibility: 'private', installs: 0, catalogKey: null, originId: null, official: false, installed: false,
    runs: 0, hasKnowledge: false, updatedAt: at, ...extra,
  }
}

function skillRow(id: string, name: string) {
  return {
    id, name, slug: id, description: `${name} 절차입니다.`, whenToUse: `${name}이 필요할 때`, body: `# ${name}`, catalogKey: null,
    source: 'personal', kinds: ['chat', 'report'], requiredTools: [], estimatedTokens: 600, version: '1.0.0', enabled: true,
    visibility: 'private', installs: 0, originId: null, updatedAt: at, scripts: [],
  }
}

const savedSteps: Step[] = [
  { id: 's1', kind: 'agent', ref: MINUTES, settings: {} },
  { id: 's2', kind: 'check', ref: 'grounding-check', settings: {} },
  { id: 's3', kind: 'agent', ref: MAIL, settings: { instruction: '실행 과제와 담당자만 뽑아 메일 초안을 씁니다.' } },
]

const catalogue = {
  maxSteps: 12,
  skills: [{ ref: 'date-calc', name: '날짜 계산', description: '', summary: '상대 날짜를 풉니다.' }],
  checks: [{ ref: 'grounding-check', name: '근거 확인', description: '수치·날짜가 자료에 있는지 대조합니다.', needs: ['document', 'answer'] }],
  human: [{ ref: 'outline', name: '사람 확인', description: '확인받고 넘깁니다.' }],
  exportFormats: ['docx', 'pdf'],
  reviewModels: [{ id: 'fixture/judge', label: 'Fixture Judge' }],
  condition_values: [
    { step_kind: 'agent', key: 'action_items', label: '실행 과제 수', type: 'number' },
    { step_kind: 'check', key: 'flagged', label: '표시한 곳 수', type: 'number' },
  ],
}

/** The server's graph checks, as far as these screens need them. */
function verdict(stages: Step[], edges: Edge[] | null) {
  const lines: Edge[] = edges ?? stages.slice(1).map((s, i) => ({ from: stages[i].id, to: s.id }))
  const problems: { step: number | string; code: string; message: string }[] = []
  stages.forEach((s, i) => {
    if (i > 0 && !lines.some((l) => l.to === s.id)) problems.push({ step: s.id, code: 'unreachable_step', message: '시작에서 닿지 않는 단계입니다. 앞 단계와 이으세요.' })
    if (s.kind === 'condition' && s.branches?.some((b) => !lines.some((l) => l.from === s.id && l.branch === b.key))) {
      problems.push({ step: s.id, code: 'branch_dead_end', message: '모든 갈래가 다음 단계로 이어져야 합니다.' })
    }
  })
  return { ok: problems.length === 0, surface: 'report', problems, warnings: [] }
}

function run(id: string, status: string, steps: [string, string, string, string, string?][]) {
  return {
    id, status, started_at: at, ended_at: status === 'running' ? null : at, session_id: 'a5555555555555555555555555555555',
    steps: steps.map(([step_id, s, input_summary, output_summary, note]) => ({
      step_id, status: s, started_at: at, ended_at: null, input_summary, output_summary, note: note ?? '', branch_taken: null,
    })),
  }
}

async function fixture(page: Page, testInfo: TestInfo, { withMade = false } = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const problems: string[] = []
  const processes: Record<string, unknown>[] = []
  const patches: { id: string; body: Record<string, unknown> }[] = []
  let runReads = 0
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  const agents = [
    agentRow(CHAIN, '회의 후속 처리', 'report', {
      process: { mode: 'custom', base: null, bundles: [], chain: [], review: '', needsReview: false, steps: savedSteps, pipelineId: 'p1' },
    }),
    agentRow(MINUTES, '회의록 정리', 'report', { model: 'fixture/base', skillIds: ['k1', 'k2'], activeSkillIds: ['k1'] }),
    agentRow(MAIL, '후속 메일 초안', 'chat', { skillIds: ['k2'], activeSkillIds: [] }),
    ...(withMade ? [agentRow(MADE, '빠진 정보 질문 에이전트', 'chat', { skillIds: ['k1'], activeSkillIds: ['k1'] })] : []),
  ]
  const skills = [skillRow('k1', '빠진 정보 질문'), skillRow('k2', '메일 문체')]

  await page.context().routeWebSocket(/^(?!wss?:\/\/[^/]+\/\?token=).*$/, (socket) => { unexpected.push(`WebSocket ${socket.url()}`); socket.close() })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = () => (request.postDataJSON() ?? {}) as Record<string, unknown>
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: ME, name: 'Chain fixture', email: 'fixture@example.test', role: 'user', status: 'active', monthlyCredits: 1000,
        creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
        preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report'], privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'fixture/base', label: 'Fixture',
      name: 'Fixture', vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report'], modality: 'chat', dataBoundary: 'external',
      creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }], defaultChatModel: 'fixture/base',
      litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [] })
    if (method === 'GET' && path === '/agents') return route.fulfill({ json: agents })
    if (method === 'GET' && path === '/skills') return route.fulfill({ json: skills })
    if (method === 'GET' && path === '/user-pipelines/catalogue') return route.fulfill({ json: catalogue })
    if (method === 'POST' && path === '/user-pipelines/validate') {
      const body = json()
      return route.fulfill({ json: verdict(body.stages as Step[], (body.edges as Edge[] | null) ?? null) })
    }
    if (method === 'PUT' && path === `/agents/${CHAIN}/process`) {
      const body = json()
      processes.push(body)
      return route.fulfill({ json: { mode: 'custom', base: null, bundles: [], chain: [], review: '', needsReview: false,
        steps: body.stages, edges: body.edges, pipelineId: 'p1', warnings: [], agent: {} } })
    }
    if (method === 'PATCH' && /^\/agents\/[^/]+$/.test(path)) {
      const id = path.split('/')[2]
      patches.push({ id, body: json() })
      return route.fulfill({ json: { ...agents.find((a) => a.id === id), ...json() } })
    }
    if (method === 'GET' && path === '/user-pipelines/p1/runs') return route.fulfill({ json: [
      run('r2', 'running', []),
      run('r1', 'failed', []),
    ] })
    if (method === 'GET' && path === '/user-pipelines/runs/r2') {
      runReads += 1
      const done = runReads > 1
      return route.fulfill({ json: run('r2', done ? 'done' : 'running', [
        ['s1', 'done', '주간회의 메모.txt', '회의록 1부'],
        ['s2', 'done', '회의록 정리의 회의록 1부', '표시를 붙인 회의록', '메모에 없는 문장 2곳에 「(확인 필요)」를 붙였습니다.'],
        ['s3', done ? 'done' : 'running', '표시를 붙인 회의록', done ? '메일 초안' : ''],
      ]) })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/projects', '/memory', '/tools', '/connectors', '/connectors/catalog', '/jobs', '/designs', '/shares',
      '/design-templates', '/skills/store', '/artifacts'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  return { unexpected, problems, processes, patches, runReads: () => runReads }
}

/** Screens for a person to look at, when `CHAIN_SHOTS` names a folder. */
async function shot(page: Page, testInfo: TestInfo, name: string) {
  if (shotsDir) await page.screenshot({ path: `${shotsDir}/${viewport(testInfo)}-${name}.png` })
}

async function openCanvas(page: Page, query = '') {
  await page.goto(`/customize/agents/${CHAIN}/chain${query}`)
  const canvas = page.getByTestId('chain-canvas')
  await expect(canvas).toBeVisible({ timeout: 15_000 })
  return canvas
}

const node = (page: Page, id: string) => page.locator(`[data-testid="chain-node"][data-step="${id}"]`)
const palette = (page: Page) => page.getByRole('complementary', { name: '단계 목록' })
const panel = (page: Page) => page.getByRole('complementary', { name: '선택한 단계' })

test('build a chain on the canvas: add, drag, connect, skills, save the same steps', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await openCanvas(page)
  await expect(page.getByTestId('chain-node')).toHaveCount(4)
  await expect(node(page, 's1')).toContainText('회의록 정리')
  // Skills under an agent: filled = 항상 적용, dashed = 필요할 때.
  const chips = node(page, 's1').locator('..').getByTestId('skill-chip')
  await expect(chips).toHaveCount(2)
  await expect(chips.nth(0)).toHaveAttribute('data-pinned', 'true')
  await expect(chips.nth(1)).toHaveAttribute('data-pinned', 'false')

  // Click: added after the selected step.
  await node(page, 's3').click()
  await expect(panel(page).getByText('후속 메일 초안').first()).toBeVisible()
  await palette(page).getByRole('button', { name: 'PDF·Word 내보내기' }).click()
  await expect(page.getByTestId('chain-node')).toHaveCount(5)
  await expect(panel(page)).toContainText('내보내기')

  // Drag: placed loose, so it is flagged until a line reaches it.
  await palette(page).getByRole('button', { name: '사람 확인' }).dragTo(page.getByTestId('chain-canvas'), { targetPosition: { x: 200, y: 320 } })
  await expect(page.getByTestId('chain-node')).toHaveCount(6)
  await expect(page.getByTestId('chain-banner')).toContainText('확인할 단계가 1개 있습니다')
  await expect(node(page, 's5').getByTestId('node-problem')).toBeVisible()

  // Keyboard way: 다음 단계 from the export step.
  await node(page, 's4').click()
  await panel(page).getByTestId('next-step').selectOption('s5')
  await expect(page.getByTestId('chain-banner')).toHaveCount(0)

  // 메일 문체 on 후속 메일 초안: 필요할 때 → 항상 적용.
  await node(page, 's3').click()
  await panel(page).getByRole('radiogroup', { name: '메일 문체 적용 방식' }).getByRole('radio', { name: '항상 적용' }).click()
  await expect(node(page, 's3').locator('..').getByTestId('skill-chip')).toHaveAttribute('data-pinned', 'true')
  await panel(page).getByLabel('모델').selectOption('fixture/base')
  await shot(page, testInfo, 'edit')

  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect(page.getByText('저장했습니다.')).toBeVisible()
  const saved = state.processes.at(-1) as { mode: string; stages: Step[]; edges: Edge[] | null }
  expect(saved.mode).toBe('custom')
  expect(saved.stages.map((s) => `${s.id}:${s.kind}`)).toEqual(['s1:agent', 's2:check', 's3:agent', 's4:export', 's5:human'])
  expect(saved.edges).toBeNull()
  expect(saved.stages[2].settings).toEqual({ instruction: '실행 과제와 담당자만 뽑아 메일 초안을 씁니다.' })
  expect(state.patches).toEqual([{ id: MAIL, body: { activeSkillIds: ['k2'], skillIds: ['k2'], model: 'fixture/base' } }])

  // The same steps as a list.
  await page.getByRole('tab', { name: '목록으로 보기' }).click()
  await expect(page.getByTestId('chain-list').getByTestId('pipeline-step')).toHaveText([/회의록 정리/, /근거 확인/, /후속 메일 초안/, /내보내기/, /사람 확인/])
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('a condition step: rule from a preceding value, two branches, saved as edges', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await openCanvas(page)
  await node(page, 's1').click()
  await palette(page).getByRole('button', { name: '조건' }).click()
  const cond = node(page, 's4')
  await expect(cond).toContainText('조건')
  // Its 예 took over the old line; 아니오 leads nowhere yet.
  await expect(page.getByTestId('chain-banner')).toContainText('모든 갈래가 다음 단계로 이어져야 합니다')

  const side = panel(page)
  await side.getByLabel('이름').fill('실행 과제가 있나?')
  await expect(side.getByRole('radio', { name: /규칙으로/ })).toHaveAttribute('aria-checked', 'true')
  const value = side.getByTestId('condition-rule').getByLabel('값')
  await expect(value.locator('option')).toHaveText(['값 고르기', '회의록 정리 · 실행 과제 수'])
  await value.selectOption({ label: '회의록 정리 · 실행 과제 수' })
  await side.getByLabel('비교').selectOption({ label: '보다 크면' })
  await side.getByLabel('기준').fill('0')
  const branches = side.getByTestId('condition-branch')
  await expect(branches).toHaveCount(2)
  await branches.nth(1).getByTestId('next-step').selectOption('s3')
  await expect(page.getByTestId('chain-banner')).toHaveCount(0)
  await expect(cond).toContainText('실행 과제가 있나?')
  await shot(page, testInfo, 'condition')

  // AI 판단 offers more branches; back to a rule it is 예/아니오 again.
  await side.getByRole('radio', { name: /AI 판단으로/ }).click()
  await side.getByRole('button', { name: /갈래 추가/ }).click()
  await expect(branches).toHaveCount(3)
  await side.getByRole('radio', { name: /규칙으로/ }).click()
  await expect(branches).toHaveCount(2)

  // A line back to an earlier step is refused.
  await node(page, 's3').click()
  await expect(side.getByTestId('condition-rule')).toHaveCount(0)
  await side.getByTestId('next-step').selectOption('s1')
  await expect(page.getByText('앞 단계로 되돌아가는 선(반복)은 이을 수 없습니다.')).toBeVisible()

  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect(page.getByText('저장했습니다.')).toBeVisible()
  const saved = state.processes.at(-1) as { stages: (Step & Record<string, unknown>)[]; edges: Edge[] }
  expect(saved.stages.map((s) => s.id)).toEqual(['s1', 's4', 's2', 's3'])
  expect(saved.stages[1]).toMatchObject({
    kind: 'condition', id: 's4', name: '실행 과제가 있나?', mode: 'rule',
    rule: { step: 's1', value: 'action_items', op: 'gt', target: '0' },
    branches: [{ key: 'yes', label: '예' }, { key: 'no', label: '아니오' }],
  })
  expect(saved.edges).toEqual([
    { from: 's1', to: 's4' },
    { from: 's4', to: 's2', branch: 'yes' },
    { from: 's4', to: 's3', branch: 'no' },
    { from: 's2', to: 's3' },
  ])

  // A branched order is shown, not edited, as a list.
  await page.getByRole('tab', { name: '목록으로 보기' }).click()
  await expect(page.getByTestId('chain-list')).toContainText('갈래가 있는 순서는 목록에서 볼 수만 있습니다')
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('a skill dropped opens its agent draft, and the agent made comes back as a node', async ({ page }, testInfo) => {
  await fixture(page, testInfo)
  await openCanvas(page)
  await palette(page).getByRole('button', { name: '빠진 정보 질문' }).click()
  await expect(page).toHaveURL(/\/customize\/agents\/new\?from=skill:k1&return=/)

  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await page.context().unrouteAll({ behavior: 'ignoreErrors' })
  const state = await fixture(page, testInfo, { withMade: true })
  await openCanvas(page, `?added=${MADE}`)
  await expect(page.getByTestId('chain-node')).toHaveCount(5)
  await expect(node(page, 's4')).toContainText('빠진 정보 질문 에이전트')
  await expect(page.getByText('저장 안 됨')).toBeVisible()
  expect(state.problems).toEqual([])
})

test('실행 기록: per-step status on the canvas, what a step took and gave, polled while running', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await openCanvas(page, '?view=runs')
  await expect(page.getByTestId('run-row')).toHaveText([/#2 · 진행 중/, /#1 · 실패/])
  await expect(page.getByTestId('run-status')).toContainText('2단계 끝')
  await expect(node(page, 's1')).toHaveAttribute('data-status', 'done')
  await expect(node(page, 's3')).toHaveAttribute('data-status', /running|done/)
  await node(page, 's2').click()
  const io = page.getByTestId('run-step')
  await expect(io).toContainText('근거 확인')
  await expect(io).toContainText('회의록 정리의 회의록 1부')
  await expect(io).toContainText('메모에 없는 문장 2곳에 「(확인 필요)」를 붙였습니다.')
  await expect(io).toContainText('표시를 붙인 회의록 → 후속 메일 초안')
  // Polled until it ends.
  await expect(node(page, 's3')).toHaveAttribute('data-status', 'done', { timeout: 8_000 })
  expect(state.runReads()).toBeGreaterThan(1)
  await shot(page, testInfo, 'runs')
  await io.getByRole('button', { name: '이 단계 설정 고치기' }).click()
  await expect(panel(page)).toContainText('근거 확인')
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('@phone the canvas fits a phone: canvas, panel and palette stack, nothing scrolls sideways', async ({ page }, testInfo) => {
  test.skip(viewport(testInfo) !== 'phone', 'phone width only')
  const state = await fixture(page, testInfo)
  const canvas = await openCanvas(page)
  await node(page, 's1').click()
  const pal = await palette(page).boundingBox()
  const box = await canvas.boundingBox()
  const side = await panel(page).boundingBox()
  expect(pal && box && side).toBeTruthy()
  // The canvas first, then the selected step, then what can be added.
  expect(side!.y).toBeGreaterThan(box!.y)
  expect(pal!.y).toBeGreaterThan(side!.y)
  expect(box!.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(wide).toBeLessThanOrEqual(1)
  await shot(page, testInfo, 'phone')
  expect(state.unexpected).toEqual([])
})
