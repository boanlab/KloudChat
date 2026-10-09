import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * Pipeline phase 2 screens against fixture data: the stage list in a report transcript and on the
 * 검토 tab, 「이 단계부터 다시」 streaming a new version, the question card as a waiting stage,
 * blanks and findings marked in the document (filled in place), the chat's compact stage list,
 * and an agent's pipeline on its card and in the editor's 「워크플로」 tab.
 */

test.use({ serviceWorkers: 'block' })

const at = '2026-10-08T07:00:00.000Z'
const reportSession = 'a1111111111111111111111111111111'
const gapSession = 'a2222222222222222222222222222222'
const chatSession = 'a3333333333333333333333333333333'
const artifactId = 'b1111111111111111111111111111111'
const runId = 'r5f2c0000000000000000000000000001'
const agentId = 'c1111111111111111111111111111111'

const shotsDir = process.env.PIPELINE_SHOTS ?? ''

function stage(name: string, label: string, kind: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    id: `${runId}:${name}:1`, runId, name, label, kind, status, reason: '', summary: '',
    startedAt: at, finishedAt: at, ms: 420, attempt: 1, rerunnable: true, ...extra,
  }
}

const stages = [
  stage('research', '자료 모으기', 'io', 'done', { summary: '첨부 2개 · 검색 없음', rerunnable: false }),
  stage('calc', '계산', 'compute', 'done', { summary: '값 4개 정함' }),
  stage('prepass', '사전 계산', 'compute', 'skipped', { reason: 'no prepass steps for this format' }),
  stage('figures', '도식', 'model', 'skipped', { reason: 'no figures planned' }),
  stage('draft', '본문 작성', 'model', 'done', { summary: '절 3개', ms: 18_400 }),
  stage('check:grounding', '근거 확인', 'check', 'done', { summary: '자료에 없는 값 1개를 빈칸으로' }),
  stage('verify', '검증', 'check', 'done', { summary: '고침 2건 · 남은 것 1건' }),
  stage('quality', '품질 점검', 'check', 'failed', { reason: 'judge model timed out', rerunnable: true }),
]

const evidence: Record<string, unknown> = {
  calc: { computed: [{ name: '총 사업비', value: '2억 4천만 원' }, { name: '회의 일수', value: '3일' }],
    computedBlock: '총 사업비 = 1억 2천만 원 × 2 = 2억 4천만 원\n회의 일수 = 10월 7일 – 10월 9일 = 3일' },
  'check:grounding': { findings: { before: ['ungrounded_value'], after: [] },
    changes: [{ sectionId: 's2', heading: '현황', old: '120명', new: '[인원]' }] },
  verify: { findings: { before: ['date_mismatch', 'sum_mismatch', 'ungrounded_value'], after: ['ungrounded_value'] },
    changes: [{ sectionId: 's1', heading: '개요', old: '10월 8일', new: '10월 7일' },
      { sectionId: 's3', heading: '예산', old: '2억 원', new: '2억 4천만 원' }] },
  quality: { code: [{ code: 'score = judge(sections)', stdout: '', stderr: 'TimeoutError: judge took longer than 30 s' }] },
}

const sections = [
  { id: 's1', heading: '개요', level: 1, status: 'done',
    content: '이 회의는 [회의 일시]에 본관 3층 회의실에서 열렸다. 참석자는 [참석자]이며, 사업 추진 일정과 예산을 논의했다.' },
  { id: 's2', heading: '현황', level: 1, status: 'done',
    content: '현재 사업에 참여하는 인원은 [인원]이다. 1차 시범 운영은 지난달 마쳤고, 이용자 만족도는 높은 편으로 나타났다.' },
  { id: 's3', heading: '예산', level: 1, status: 'done',
    content: '총 사업비는 2억 4천만 원으로 정했다. 예산 집행률은 지난 분기보다 크게 올라 연말까지 모두 집행할 수 있을 것으로 본다. 담당은 [담당자]가 맡는다.\n\n| 할 일 | 담당 | 기한 |\n| :--- | :--- | :--- |\n| 집행 계획 제출 | **재무팀** | 10월 말 |' },
]

const marks = {
  artifactId, version: 1, runId,
  sections: [
    { sectionId: 's2', heading: '현황', spans: [
      { kind: 'ungrounded-replaced', start: 15, end: 19, text: '[인원]', original: '120명', reason: '자료에 없는 값', stage: 'check:grounding', code: '' },
    ] },
    { sectionId: 's3', heading: '예산', spans: [
      { kind: 'check-finding', start: 20, end: 60, text: '예산 집행률은 지난 분기보다 크게 올라', original: '', reason: '자료에 없는 비교입니다', stage: 'verify', code: 'ungrounded_value' },
      // A flagged table row is stored as its source line; it is found in the rendered table.
      { kind: 'check-finding', start: 120, end: 160, text: '| 집행 계획 제출 | **재무팀** | 10월 말 |', original: '', reason: '자료에 없는 기한입니다', stage: 'verify', code: 'ungrounded_value' },
    ] },
  ],
}

const pipeline = {
  name: 'minutes', formats: ['minutes'], agents: ['minutes'], options: { search: true },
  checks: ['grounding-check', 'gap-check'],
  flows: {
    pre: [{ name: 'check:gaps', label: '빠진 정보 확인', kind: 'check', checks: ['gap-check'] }],
    plan: [{ name: 'research', label: '자료 모으기', kind: 'io' }, { name: 'outline', label: '구성', kind: 'model' }],
    write: [
      { name: 'research', label: '자료 모으기', kind: 'io' },
      { name: 'calc', label: '계산', kind: 'compute' },
      { name: 'draft', label: '본문 작성', kind: 'model' },
      { name: 'check:grounding', label: '근거 확인', kind: 'check', checks: ['grounding-check'] },
      { name: 'verify', label: '검증', kind: 'check' },
      { name: 'export', label: '내보내기', kind: 'io' },
    ],
  },
  lists: { prepass: ['date-calc'], checks: ['lead-dates'], judged: ['judge-owners'] },
}

function message(id: string, role: 'user' | 'assistant', content: string, extra: Record<string, unknown> = {}) {
  return { id, role, content, steps: null, attachments: null, variants: null, usage: null, model: 'fixture/base',
    routing: null, startedFrom: null, rating: null, artifactIds: null, failure: null, createdAt: at, ...extra }
}

function sessionRow(id: string, kind: string, title: string, messages: unknown[], extra: Record<string, unknown> = {}) {
  return { id, kind, title, projectId: null, agentId: null, model: 'fixture/base', routingMode: 'manual',
    artifactId: null, pending: null, skillIds: [], renderTemplateId: null, pinned: false, createdAt: at, updatedAt: at,
    messages, preview: null, messageCount: messages.length, made: null, ...extra }
}

const agent = {
  ownerId: 'fixture-user', ownerName: 'Fixture', id: agentId, name: '회의록 정리', slug: 'minutes',
  description: '회의 녹취와 메모로 회의록을 만듭니다.', model: '', systemPrompt: '회의록을 씁니다.', tools: [], skillIds: [],
  kinds: ['report'], surface: 'report', renderTemplateId: null, docFormat: 'minutes', designSystemId: null, guide: '',
  starters: [], shareMode: 'open', sealed: false, temperature: 0.3, color: '#168267', enabled: true, visibility: 'private',
  installs: 0, catalogKey: 'minutes', originId: null, official: false, installed: false, runs: 4, hasKnowledge: false,
  updatedAt: at,
}

function sse(events: unknown[]) {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
}

async function fixture(page: Page, testInfo: TestInfo) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const problems: string[] = []
  const posts: string[] = []
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`))
  let version = 1
  let rerunDone = false
  let artifactSections = sections
  const artifact = () => ({
    id: artifactId, title: '정기 운영 회의록', kind: 'report', version, partial: false,
    sessionId: reportSession, projectId: null, createdAt: at, updatedAt: at,
    data: { kind: 'report', title: '정기 운영 회의록', sections: artifactSections, sources: [], citationStyle: 'APA',
      wordCount: 120, marks: rerunDone ? { ...marks, version, sections: [] } : { ...marks, version } },
  })
  const run = { id: runId, pipeline: 'minutes', flow: 'write', status: 'done', parentId: null, fromStage: null,
    stages: stages.map(({ ...s }) => s) }
  const report = sessionRow(reportSession, 'report', '정기 운영 회의록', [
    message('m1', 'user', '첨부한 메모로 10월 정기 운영 회의록을 만들어 주세요.'),
    message('m2', 'assistant', '회의록을 만들었습니다.', { routing: { run }, artifactIds: [artifactId],
      steps: [{ id: 'context-files', type: 'step', label: '첨부 파일 2개 반영', status: 'done',
        files: [{ name: '메모.txt', state: 'included', keptChars: 900, totalChars: 900 },
          { name: '녹취.txt', state: 'included', keptChars: 4000, totalChars: 4000 }] }] }),
  ], { artifactId })
  const gapQuestions = [
    { id: 'gap:meeting_date', question: '회의 일시가 자료에 없습니다. 언제였나요?', options: [], detail: '회의록 머리에 적힙니다.',
      fact: 'meeting_date', label: '회의 일시', why: '회의록 머리에 적힙니다.', placeholder: '[회의 일시]', freeText: true },
    { id: 'gap:attendees', question: '참석자는 누구였나요?', options: ['운영팀 전원', '팀장 회의'], detail: '',
      fact: 'attendees', label: '참석자', why: '참석자 줄에 들어갑니다.', placeholder: '[참석자]', freeText: true },
  ]
  const waiting = { id: 'r9a1d:gap-card:1', runId: 'r9a1d', name: 'gap-card', label: '빠진 정보 질문', kind: 'io',
    status: 'waiting', reason: 'missing facts', summary: '질문 2개', startedAt: at, finishedAt: at, ms: 30, attempt: 1,
    rerunnable: false }
  const gap = sessionRow(gapSession, 'report', '분기 실적 보고서', [
    message('g1', 'user', '3분기 실적 보고서를 써 주세요.'),
    message('g2', 'assistant', '시작하기 전에 확인할 것이 있습니다.', { routing: { run: { id: 'r9a1d', pipeline: 'report',
      flow: 'pre', status: 'waiting', parentId: null, fromStage: null, stages: [
        { ...waiting, id: 'r9a1d:check:gaps:1', name: 'check:gaps', label: '빠진 정보 확인', kind: 'check', status: 'done', summary: '빠진 것 2개', reason: '' },
        waiting,
      ] } } }),
  ], { pending: { stage: 'clarify', request: '3분기 실적 보고서', attachments: [], answers: {}, runId: 'r9a1d', questions: gapQuestions } })
  const chat = sessionRow(chatSession, 'chat', '자기소개서 문항 점검', [
    message('c1', 'user', '이 자기소개서 답이 700자 안에 드는지 봐 주세요.'),
    message('c2', 'assistant', '[제출용 답안 1]\n저는 데이터로 문제를 푸는 사람입니다…\n\n공백 포함 684자로 700자 안에 듭니다.', {
      routing: { run: { id: 'rchat', pipeline: 'career-docs', flow: 'finish', status: 'done', parentId: null, fromStage: null, stages: [
        { ...stage('answer-normalize', '답 다듬기', 'compute', 'done', { summary: '' }), rerunnable: false },
        { ...stage('check:answer-grounding', '근거 확인', 'check', 'done', { summary: '자료에 없는 수치 1개 지움' }), rerunnable: false },
        { ...stage('text-fit', '글자 수 맞추기', 'compute', 'done', { summary: '684/700자' }), rerunnable: false },
        { ...stage('answer-replace', '답 바꾸기', 'io', 'skipped', { reason: 'nothing changed' }), rerunnable: false },
      ] } },
      steps: [{ id: 'skills-applied', type: 'thinking', label: '스킬 1개 적용', status: 'done', skills: [{ id: 'k1', name: '자기소개서', catalogKey: null, estimatedTokens: 900 }], estimatedTokens: 900 }] }),
  ])
  const sessions = [report, gap, chat]

  // The dev server's own HMR socket is left alone; the app opens none.
  await page.context().routeWebSocket(/^(?!wss?:\/\/[^/]+\/\?token=).*$/, (socket) => { unexpected.push(`WebSocket ${socket.url()}`); socket.close() })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method !== 'GET') posts.push(`${method} ${path}`)
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: 'Pipeline fixture', email: 'fixture@example.test', role: 'user',
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
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: sessions.map((s) => ({ ...s, messages: null })) })
    const one = sessions.find((s) => path === `/sessions/${s.id}`)
    if (method === 'GET' && one) return route.fulfill({ json: one })
    if (method === 'GET' && path === `/sessions/${reportSession}/runs/${runId}`) {
      return route.fulfill({ json: { ...run, sessionId: reportSession, messageId: 'm2', artifactId, artifactVersion: 1,
        createdAt: at, updatedAt: at, stages: run.stages.map((s) => ({ ...s, evidence: evidence[s.name] })) } })
    }
    if (method === 'POST' && path === `/sessions/${reportSession}/runs/${runId}/rerun`) {
      const { fromStage } = request.postDataJSON() as { fromStage: string }
      version += 1
      rerunDone = true
      artifactSections = sections.map((s) => s.id === 's3'
        ? { ...s, content: '총 사업비는 2억 4천만 원으로 정했다. 예산 집행률은 3분기 기준 61%다. 담당은 [담당자]가 맡는다.' } : s)
      const newRun = 'r7b10000000000000000000000000002'
      const from = stages.findIndex((s) => s.name === fromStage)
      const rerun = stages.slice(from).map((s) => ({ ...s, id: `${newRun}:${s.name}:2`, runId: newRun, attempt: 2,
        status: s.name === 'quality' ? 'done' : s.status, reason: s.name === 'quality' ? '' : s.reason,
        summary: s.name === 'quality' ? '점수 86' : s.name === 'verify' ? '고침 1건 · 남은 것 0건' : s.summary,
        evidence: s.name === 'verify' ? { findings: { before: ['ungrounded_value'], after: [] },
          changes: [{ sectionId: 's3', heading: '예산', old: '크게 올라', new: '3분기 기준 61%' }] } : undefined }))
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse([
        { type: 'run', runId: newRun, pipeline: 'minutes', flow: 'write', parentId: runId, fromStage },
        ...rerun.map((s) => ({ type: 'step', category: 'pipeline', id: `stage:${s.name}`, label: s.label, status: s.status === 'failed' ? 'error' : 'done', stage: s })),
        { type: 'section', sectionId: 's3', heading: '예산', content: artifactSections[2].content, done: true },
        { type: 'artifact', artifactId },
        { type: 'usage', inputTokens: 1200, outputTokens: 300, credits: 3 },
        { type: 'done', messageId: 'm3' },
      ]) })
    }
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: [artifact()] })
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return route.fulfill({ json: artifact() })
    if (method === 'GET' && path === `/artifacts/${artifactId}/marks`) return route.fulfill({ json: artifact().data.marks })
    if (method === 'GET' && path === `/artifacts/${artifactId}/versions`) return route.fulfill({ json: [] })
    if (method === 'PATCH' && path === `/artifacts/${artifactId}`) {
      const patch = request.postDataJSON() as { data?: { sections?: typeof sections } }
      if (patch.data?.sections) artifactSections = patch.data.sections
      version += 1
      return route.fulfill({ json: artifact() })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: { report: 1 }, total: 1 } })
    if (method === 'GET' && path === '/agents') return route.fulfill({ json: [agent] })
    if (method === 'GET' && path === `/agents/${agentId}/pipeline`) return route.fulfill({ json: pipeline })
    if (method === 'GET' && /^\/agents\/[^/]+\/knowledge/.test(path)) return route.fulfill({ json: [] })
    if (method === 'GET' && (path.startsWith('/sessions/') && (path.endsWith('/messages') || path.endsWith('/jobs')))) return route.fulfill({ json: [] })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/projects', '/skills', '/memory', '/tools', '/connectors', '/connectors/catalog',
      '/jobs', '/designs', '/shares', '/design-templates', '/skills/store',
      '/user-pipelines', '/user-pipelines/store'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  return { unexpected, problems, posts }
}

function shooter(page: Page, testInfo: TestInfo) {
  const dir = shotsDir ? `${shotsDir}/${testInfo.project.name}` : testInfo.outputPath('shots')
  let n = 0
  return async (name: string, full = false) => {
    n += 1
    await page.screenshot({ path: `${dir}/${testInfo.title.split(':')[0]}-${String(n).padStart(2, '0')}-${name}.png`, fullPage: full })
  }
}

const panel = (page: Page) => page.locator('[data-panel="artifact"]')

test('report: stage list, evidence, marks, fill, rerun', async ({ page }, testInfo) => {
  const phone = testInfo.project.name === 'phone'
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)
  await page.goto(`/s/${reportSession}?artifact=${artifactId}`)
  await expect(page.getByText('회의록을 만들었습니다.')).toBeVisible({ timeout: 15_000 })
  const timeline = page.getByRole('button', { name: /보고서를 썼습니다/ }).first()
  if (phone) {
    // The panel covers the transcript on a phone; the transcript is read first.
    const close = panel(page).getByRole('button', { name: /닫기/ }).first()
    if (await close.isVisible().catch(() => false)) await close.click()
  }
  await timeline.click()
  const list = page.getByTestId('stage-list').first()
  await expect(list).toBeVisible()
  await shot('transcript-stages')
  // Status icons say what they mean.
  await expect(list.getByRole('img', { name: '실패' })).toBeVisible()
  await list.getByRole('button', { name: '해당 없는 단계 2개' }).click()
  await list.getByRole('button', { name: '검증 근거 보기', exact: true }).click()
  await expect(list.getByText('10월 7일').first()).toBeVisible()
  await list.getByRole('button', { name: '계산 근거 보기', exact: true }).click()
  await expect(list.getByText('2억 4천만 원').first()).toBeVisible()
  await list.locator('[data-stage="verify"]').scrollIntoViewIfNeeded()
  await shot('evidence-open')
  await list.getByRole('button', { name: '품질 점검 근거 보기', exact: true }).click()
  await list.getByRole('button', { name: '검증 단계 메뉴' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('menuitem', { name: '이 단계부터 다시' })).toBeVisible()
  await shot('rerun-menu')
  await page.keyboard.press('Escape')

  if (!phone) {
    // The document: blanks, the blanked value, a flagged sentence.
    await expect(panel(page).getByRole('navigation', { name: '문서 표시 이동' })).toBeVisible({ timeout: 10_000 })
    await expect(panel(page).getByText('빈칸 3개')).toBeVisible()
    await expect(panel(page).getByText('확인할 곳 3개')).toBeVisible()
    await shot('doc-marks')
    await panel(page).getByRole('button', { name: '다음 표시' }).click()
    await panel(page).getByRole('button', { name: '다음 표시' }).click()
    await panel(page).getByRole('button', { name: '다음 표시' }).click()
    await page.waitForTimeout(500)
    await panel(page).locator('.doc-ungrounded').first().hover()
    await expect(panel(page).getByText('자료에 없음: 원래 값 120명 → [인원] · 근거 확인')).toBeVisible()
    await shot('ungrounded-tooltip')
    await panel(page).locator('[data-marker]').first().hover()
    await shot('finding-marker')
    // Fill a blank where it stands.
    await panel(page).getByRole('button', { name: /빈칸 \[회의 일시\], 눌러서 채우기/ }).click()
    const fill = panel(page).getByRole('dialog', { name: '[회의 일시] 채우기' })
    await expect(fill).toBeVisible()
    await fill.getByRole('textbox').fill('2026년 10월 7일 14시')
    await shot('fill-blank')
    await page.keyboard.press('Enter')
    await expect(panel(page).getByText('2026년 10월 7일 14시')).toBeVisible()
    await expect(panel(page).getByText('빈칸 2개')).toBeVisible()
    await shot('filled')

    // The 검토 tab's 작업 단계.
    await panel(page).getByRole('tab', { name: '검토', exact: true }).click()
    await panel(page).getByRole('button', { name: /작업 단계/ }).click()
    const dialog = page.getByRole('dialog', { name: '작업 단계' })
    await expect(dialog).toBeVisible()
    await shot('review-stages')
    await page.keyboard.press('Escape')
  }

  // 「이 단계부터 다시」 → confirm → the new run streams in.
  await list.getByRole('button', { name: '검증 단계 메뉴' }).click()
  await page.getByRole('menuitem', { name: '이 단계부터 다시' }).click()
  await expect(list.getByRole('button', { name: '다시 만들기' })).toBeVisible()
  await shot('rerun-confirm')
  await list.getByRole('button', { name: '다시 만들기' }).click()
  await expect(page.getByText('「검증」 단계부터 다시 만들었습니다.')).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: /보고서를 썼습니다/ }).last().click().catch(() => undefined)
  await page.waitForTimeout(400)
  await shot('after-rerun')
  expect(state.posts).toContain(`POST /sessions/${reportSession}/runs/${runId}/rerun`)
  expect(state.problems).toEqual([])
  if (state.unexpected.length) console.log('unexpected', state.unexpected)
})

test('gap: question card in the waiting stage', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)
  await page.goto(`/s/${gapSession}`)
  await expect(page.getByText('회의 일시가 자료에 없습니다. 언제였나요?')).toBeVisible({ timeout: 15_000 })
  // Once: inside the stage list, not again at the end of the transcript.
  await expect(page.getByText('회의 일시가 자료에 없습니다. 언제였나요?')).toHaveCount(1)
  await expect(page.getByTestId('stage-list').getByText('회의 일시가 자료에 없습니다. 언제였나요?')).toBeVisible()
  await page.getByLabel('참석자는 누구였나요?').fill('운영팀 5명')
  await shot('gap-card', true)
  expect(state.problems).toEqual([])
})

test('chat: compact stage list beside tool steps', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)
  await page.goto(`/s/${chatSession}`)
  await expect(page.getByText('공백 포함 684자로 700자 안에 듭니다.')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: /답했습니다/ }).first().click()
  await expect(page.locator('[data-category="pipeline"]')).toBeVisible()
  // Chat rows offer no rerun.
  await expect(page.getByRole('button', { name: /단계 메뉴/ })).toHaveCount(0)
  await shot('chat-stages')
  expect(state.problems).toEqual([])
})

test('agents: pipeline chain and the 워크플로 tab', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)
  await page.goto(`/customize/agents/${agent.id}`)
  await expect(page.getByLabel('워크플로: 자료 → 질문 → 계산 → 작성 → 검사 → 내보내기').first()).toBeVisible({ timeout: 15_000 })
  await shot('agent-card')
  await page.getByRole('region', { name: '설정' }).getByText('워크플로 연결', { exact: true }).click()
  // A read-only summary; flows are edited on the 「워크플로」 page (user-pipelines.spec.ts).
  const summary = page.getByTestId('agent-flow-summary')
  await expect(summary.getByLabel('워크플로: 자료 → 질문 → 계산 → 작성 → 검사 → 내보내기')).toBeVisible()
  await expect(summary.getByRole('link', { name: '워크플로 편집' })).toBeVisible()
  await shot('agent-flow-tab')
  if (testInfo.project.name !== 'phone') {
    await page.keyboard.press('Escape')
    await page.goto('/')
    await page.getByRole('button', { name: '보고서', exact: true }).first().click()
    await expect(page.getByLabel('워크플로: 자료 → 질문 → 계산 → 작성 → 검사 → 내보내기')).toBeVisible({ timeout: 15_000 })
    await page.getByText('에이전트에게 맡기기').scrollIntoViewIfNeeded()
    await page.waitForTimeout(400)
    await shot('home-agent-picker')
  }
  expect(state.problems).toEqual([])
})

test('home: composer docked at the bottom, empty textarea without a scrollbar', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const shot = shooter(page, testInfo)
  const stamp = process.env.HOME_SHOT_TAG ?? 'after'
  await page.goto('/')
  await page.getByRole('button', { name: '보고서', exact: true }).first().click()
  const box = page.locator('textarea[data-composer]')
  await expect(box).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(400)
  await shot(`${stamp}-home`)
  const overflow = await box.evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight, style: getComputedStyle(el).overflowY }))
  console.log('home textarea', JSON.stringify(overflow))
  const docked = await box.evaluate((el) => window.innerHeight - el.getBoundingClientRect().bottom)
  console.log('home composer gap to bottom', docked)
  if (stamp === 'after') {
    expect(overflow.style).toBe('hidden')
    expect(docked).toBeLessThan(140)
  }
  await page.goto(`/s/${chatSession}`)
  await expect(page.getByText('공백 포함 684자로 700자 안에 듭니다.')).toBeVisible({ timeout: 15_000 })
  await shot(`${stamp}-session`)
  const inSession = await page.locator('textarea[data-composer]').evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight, style: getComputedStyle(el).overflowY }))
  console.log('session textarea', JSON.stringify(inSession))
  expect(state.problems).toEqual([])
})
