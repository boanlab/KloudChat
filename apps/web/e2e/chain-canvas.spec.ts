import { expect, test, type Page, type TestInfo } from '@playwright/test'
import api from './fixtures/chain-canvas.json' with { type: 'json' }
import { viewport } from './project'
import { shellRead } from './shell-mocks'

/**
 * Answers come from `fixtures/chain-canvas.json`, which the API test
 * `test_the_chain_canvas_fixture_is_the_apis.py` keeps equal to what the API sends (catalogue,
 * problem sentences, runs read per step).
 *
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

type Step = {
  id: string; kind: string; ref: string; settings?: Record<string, unknown>; name?: string; mode?: string
  rule?: Record<string, unknown>; branches?: { key: string; label: string }[]
}
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

const catalogue = { ...api.catalogue, reviewModels: [{ id: 'fixture/judge', label: 'Fixture Judge' }] }

const KIND = api.kindWords as Record<string, string>

/** The server's graph checks, as far as these screens need them, in its words (`compose.validate`). */
function verdict(stages: Step[], edges: Edge[] | null, agents: { id: string; name: string; surface: string }[]) {
  const lines: Edge[] = edges ?? stages.slice(1).map((s, i) => ({ from: stages[i].id, to: s.id }))
  const title = (s: Step) =>
    s.kind === 'agent' ? (agents.find((a) => a.id === s.ref)?.name ?? '에이전트')
      : s.kind === 'check' ? (catalogue.checks.find((c) => c.ref === s.ref)?.name ?? s.ref)
        : s.kind === 'human' ? (catalogue.human.find((c) => c.ref === s.ref)?.name ?? '사람 확인')
          : s.kind === 'condition' ? (s.name || '조건') : KIND[s.kind]
  const problems: { step: number; stepId: string; code: string; message: string }[] = []
  const say = (i: number, code: keyof typeof api.messages, values: Record<string, string> = {}) => {
    const at = `${i + 1}번 ${KIND[stages[i].kind]}(${title(stages[i])})`
    const message = Object.entries({ at, ...values }).reduce((m, [k, v]) => m.replace(`{${k}}`, v), api.messages[code])
    problems.push({ step: i + 1, stepId: stages[i].id, code, message })
  }
  const reached = new Set([stages[0]?.id])
  for (let changed = true; changed; ) {
    changed = false
    for (const l of lines) if (reached.has(l.from) && !reached.has(l.to)) changed = reached.add(l.to) && true
  }
  let made = ''
  stages.forEach((s, i) => {
    if (!reached.has(s.id)) return say(i, 'unreachable_step')
    if (s.kind === 'agent') made = agents.find((a) => a.id === s.ref)?.surface ?? ''
    if (s.kind === 'human' && s.ref === 'gaps' && !stages.slice(i + 1).some((x) => x.kind === 'agent')) say(i, 'needs_agent_after')
    if (s.kind === 'export' && made !== 'report') say(i, 'needs_product', { word: '문서가', fix: api.productFixes['문서가'] })
    if (s.kind !== 'condition') return
    const rule = s.rule as { step?: string; op?: string; target?: string } | undefined
    if (s.mode === 'rule' && !rule?.step) say(i, 'condition_incomplete', { what: '규칙이 읽을 값을 이 조건 앞 단계에서 골라 주세요.' })
    for (const b of s.branches ?? []) if (!lines.some((l) => l.from === s.id && l.branch === b.key)) say(i, 'branch_dead_end', { label: b.label })
  })
  return { ok: problems.length === 0, surface: 'report', problems, warnings: [] }
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
      // `bases.process_of` for the owner: titled steps, edges (null: in order), the pipeline id.
      process: { mode: 'custom', base: null, bundles: [], chain: ['회의록 정리', '근거 대조', '후속 메일 초안'], review: '',
        needsReview: false, steps: savedSteps.map((s) => ({ ...s, title: '' })), edges: null, pipelineId: 'p1' },
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
      const stages = body.stages as Step[]
      return route.fulfill({ json: { ...verdict(stages, (body.edges as Edge[] | null) ?? null, agents), titles: stages.map(() => '') } })
    }
    if (method === 'PUT' && path === `/agents/${CHAIN}/process`) {
      const body = json()
      processes.push(body)
      return route.fulfill({ json: { mode: 'custom', base: null, bundles: [], chain: [], review: '', needsReview: false,
        steps: body.stages, edges: body.edges, pipelineId: 'p1', warnings: [],
        agent: { surface: 'report', kinds: ['report'], docFormat: null, renderTemplateId: null, baseKey: null, pipelineId: 'p1',
          checkSkills: [], checkOptions: {}, bundles: null } } })
    }
    if (method === 'PATCH' && /^\/agents\/[^/]+$/.test(path)) {
      const id = path.split('/')[2]
      patches.push({ id, body: json() })
      return route.fulfill({ json: { ...agents.find((a) => a.id === id), ...json() } })
    }
    if (method === 'GET' && path === '/user-pipelines/p1/runs') return route.fulfill({ json: api.runs })
    const one = api.runs.find((r) => path === `/user-pipelines/runs/${r.id}`)
    if (method === 'GET' && one) {
      runReads += 1
      return route.fulfill({ json: one })
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
  await palette(page).getByRole('button', { name: '검토' }).click()
  await expect(page.getByTestId('chain-node')).toHaveCount(5)
  await expect(panel(page)).toContainText('검토')

  // Drag: placed loose, so it is flagged until a line reaches it.
  await palette(page).getByRole('button', { name: '개요 확인' }).dragTo(page.getByTestId('chain-canvas'), { targetPosition: { x: 200, y: 320 } })
  await expect(page.getByTestId('chain-node')).toHaveCount(6)
  await expect(page.getByTestId('chain-banner')).toContainText('확인할 단계가 1개 있습니다')
  await expect(node(page, 's5').getByTestId('node-problem')).toBeVisible()

  // Keyboard way: 다음 단계 from the review step.
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
  expect(saved.stages.map((s) => `${s.id}:${s.kind}`)).toEqual(['s1:agent', 's2:check', 's3:agent', 's4:review', 's5:human'])
  expect(saved.edges).toBeNull()
  expect(saved.stages[2].settings).toEqual({ instruction: '실행 과제와 담당자만 뽑아 메일 초안을 씁니다.' })
  expect(state.patches).toEqual([{ id: MAIL, body: { activeSkillIds: ['k2'], skillIds: ['k2'], model: 'fixture/base' } }])

  // The same steps as a list.
  await page.getByRole('tab', { name: '목록으로 보기' }).click()
  await expect(page.getByTestId('chain-list').getByTestId('pipeline-step')).toHaveText([/회의록 정리/, /근거 대조/, /후속 메일 초안/, /검토/, /개요 확인/])
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
  const side = panel(page)
  await expect(page.getByTestId('chain-banner')).toContainText('확인할 단계가 1개 있습니다')
  await expect(side.getByRole('alert')).toContainText('규칙이 읽을 값을 이 조건 앞 단계에서 골라 주세요.')
  await expect(side.getByRole('alert')).toContainText('「아니오」 갈래 뒤에 이어질 단계가 없습니다')

  await side.getByLabel('이름').fill('실행 과제가 있나?')
  await expect(side.getByRole('radio', { name: /규칙으로/ })).toHaveAttribute('aria-checked', 'true')
  const value = side.getByTestId('condition-rule').getByLabel('값')
  // The request and what the steps before it leave behind (catalogue `condition_values`).
  await expect(value.locator('option')).toHaveText(['값 고르기', '요청 · 첨부 수', '요청 · 첨부 형식', '요청 · 요청 글',
    '회의록 정리 · 결과 종류(문서·답)', '회의록 정리 · 쓴 장 수', '회의록 정리 · 결과 글자 수', '회의록 정리 · 결과 글'])
  await value.selectOption({ label: '회의록 정리 · 쓴 장 수' })
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
  // Up to the catalogue's `maxBranches` (4).
  await side.getByRole('button', { name: /갈래 추가/ }).click()
  await side.getByRole('button', { name: /갈래 추가/ }).click()
  await expect(branches).toHaveCount(4)
  await expect(side.getByRole('button', { name: /갈래 추가/ })).toBeDisabled()
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
    rule: { step: 's1', value: 'sections', op: 'gt', target: '0' },
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

test('실행 기록: per-step status on the canvas, what a step took and gave, read again on 새로 고침', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await openCanvas(page, '?view=runs')
  await expect(page.getByTestId('run-row')).toHaveText([/#2 · 기다리는 중.*주간회의 메모로 회의록을.*첨부 1개/, /#1 · 실패.*기획회의 메모 정리/])
  await expect(page.getByTestId('run-status')).toContainText('#2 기다리는 중')
  await expect(page.getByTestId('run-status')).toContainText('2단계 끝 · 1단계 대기')
  await expect(node(page, 's1')).toHaveAttribute('data-status', 'done')
  await expect(node(page, 's3')).toHaveAttribute('data-status', 'waiting')
  await node(page, 's2').click()
  const io = page.getByTestId('run-step')
  await expect(io).toContainText('근거 대조')
  await expect(io).toContainText('「회의록 정리」 회의록 1부')
  await expect(io).toContainText('메모에 없는 문장 2곳 표시 → 후속 메일 초안')
  // A finished step says it finished, never 「아직 없습니다」.
  await expect(io).toContainText('마쳤습니다 · 메모에 없는 문장 2곳 표시')
  await expect(io).not.toContainText('아직 없습니다')
  await shot(page, testInfo, 'runs')
  // One read per run picked; 새로 고침 reads again.
  expect(state.runReads()).toBe(1)
  await page.getByRole('button', { name: '새로 고침' }).click()
  await expect.poll(state.runReads).toBe(2)
  await page.getByTestId('run-row').nth(1).click()
  await expect(node(page, 's2')).toHaveAttribute('data-status', 'failed')
  await expect(node(page, 's3')).toHaveAttribute('data-status', 'skipped')
  await node(page, 's2').click()
  await expect(io).toContainText('이 단계에서 멈췄습니다')
  await io.getByRole('button', { name: '이 단계 설정 고치기' }).click()
  await expect(panel(page)).toContainText('근거 대조')
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('@phone on a phone the step list is a sheet opened beside the canvas, and nothing scrolls sideways', async ({ page }, testInfo) => {
  test.skip(viewport(testInfo) !== 'phone', 'phone width only')
  const state = await fixture(page, testInfo)
  const canvas = await openCanvas(page)
  await node(page, 's1').click()
  const box = await canvas.boundingBox()
  const side = await panel(page).boundingBox()
  expect(side!.y).toBeGreaterThan(box!.y)
  expect(box!.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  await expect(palette(page)).toBeHidden()
  const add = page.getByRole('button', { name: '단계 추가' })
  // Within one screen of the canvas.
  const at = await add.boundingBox()
  expect(at!.y - (box!.y + box!.height)).toBeLessThan(120)
  await add.click()
  const sheet = page.getByRole('dialog', { name: '단계 추가' })
  await sheet.getByRole('button', { name: '검토' }).click()
  await expect(sheet).toBeHidden()
  await expect(page.getByTestId('chain-node')).toHaveCount(5)
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(wide).toBeLessThanOrEqual(1)
  await shot(page, testInfo, 'phone')
  expect(state.unexpected).toEqual([])
})

test('a step the server would refuse is flagged in the palette before it is added, with how to fix it', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await openCanvas(page)
  await expect(palette(page).getByTestId('palette-item').first()).toBeVisible()
  // After 후속 메일 초안 (a chat agent): an export has no document, a question card no agent after it.
  await node(page, 's3').click()
  const exporting = palette(page).getByRole('button', { name: 'PDF·Word 내보내기' })
  await expect(exporting).toHaveAttribute('data-warn', 'true', { timeout: 8_000 })
  await expect(exporting).toHaveAccessibleDescription(/앞 단계에서 만든 문서가 있어야 합니다\. 이 단계 앞에 보고서\(문서\)를 쓰는 에이전트를 두세요\./)
  const asking = palette(page).getByRole('button', { name: '확인 질문' })
  await expect(asking).toHaveAccessibleDescription(/뒤에 에이전트 단계가 있어야 합니다\. 받은 답으로 쓸 에이전트를 이 단계 뒤에 이으세요\./)
  // Before 회의록 정리 the card is fine.
  await node(page, '__start').click()
  await expect(asking).not.toHaveAttribute('data-warn', 'true', { timeout: 8_000 })
  // Added anyway, the node is flagged before saving.
  await node(page, 's3').click()
  await exporting.click()
  await expect(node(page, 's4').getByTestId('node-problem')).toBeVisible()
  await expect(page.getByTestId('chain-banner')).toContainText('이 단계 앞에 보고서(문서)를 쓰는 에이전트를 두세요')
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('화면에 맞추기 shows every step of a long chain, and new branch steps do not overlap', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const canvas = await openCanvas(page)
  for (const name of ['검토', '개요 확인', '날짜 계산', '카피 점검']) {
    await palette(page).getByRole('button', { name }).click()
  }
  await expect(page.getByTestId('chain-node')).toHaveCount(8)
  await page.getByRole('button', { name: '화면에 맞추기' }).click()
  await page.waitForTimeout(400)
  const frame = (await canvas.boundingBox())!
  for (const box of await page.getByTestId('chain-node').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().toJSON()))) {
    expect(box.left).toBeGreaterThanOrEqual(frame.x - 1)
    expect(box.right).toBeLessThanOrEqual(frame.x + frame.width + 1)
  }

  // An AI condition with three branches, a step added on each: no two nodes overlap.
  await node(page, 's1').click()
  await palette(page).getByRole('button', { name: '조건' }).click()
  const side = panel(page)
  await side.getByRole('radio', { name: /AI 판단으로/ }).click()
  await side.getByRole('button', { name: /갈래 추가/ }).click()
  for (const name of ['검토', '날짜 계산', '근거 대조']) {
    await node(page, 's8').click()
    await palette(page).getByRole('button', { name }).click()
  }
  const boxes = await page.getByTestId('chain-node').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().toJSON()))
  for (const [i, a] of boxes.entries()) {
    for (const b of boxes.slice(i + 1)) {
      const apart = a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top
      expect(apart).toBe(true)
    }
  }
  await shot(page, testInfo, 'long')
  expect(state.problems).toEqual([])
})
