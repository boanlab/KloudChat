import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * 지식 서가: the conversation's 자료 panel (per-source switches kept per conversation, guides
 * with suggested questions), a citation opening the source viewer at the cited passage, the
 * project's 스튜디오 (estimate before generating, a quiz played and retried) and 메모함
 * (a note edited, a lost save race, a note made a source). The API is answered here.
 */

test.use({ serviceWorkers: 'block' })

const projectId = '55555555555555555555555555555555'
const sessionId = '66666666666666666666666666666666'
const at = '2026-11-13T00:00:00.000Z'
const shots = process.env.SHELF_SHOTS

const user = {
  id: 'fixture-user', name: '서가 검증', email: 'fixture@example.test', role: 'user', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}

const source = (id: string, name: string, kind: string, mime: string) => ({
  id, name, size: 2048, mime, tokens: 640, projectId, sessionId: null, sourceUrl: null, origin: 'upload',
  preview: '논리 시계', error: null, indexed: true, ocrState: null, ocrTotal: 0, ocrDone: 0, ocrReview: 0,
  createdAt: at, kind, digest: null, guideState: 'none', readable: true,
})
const SOURCES = [
  source('f1', '02강 시간과 시계.pdf', 'file', 'application/pdf'),
  source('f2', '05강 래프트.mp4', 'video', 'video/mp4'),
  source('f3', '회의 메모.txt', 'text', 'text/plain'),
]

const ANSWER = [
  '벡터 시계로는 두 사건이 동시에 일어났는지 판단할 수 있습니다 [1]. 래프트는 리더를 뽑아 로그를 복제합니다 [2].',
  '',
  '### 출처',
  '- [1] 02강 시간과 시계.pdf · 2쪽',
  '- [2] 05강 래프트.mp4',
].join('\n')

const QUIZ = {
  id: 'q1', projectId, kind: 'quiz', title: '시계와 합의 퀴즈', state: 'done', error: '',
  sourceIds: ['f1', 'f2'], options: { count: 2 }, artifactId: null, sessionId: null,
  model: 'strict-local/qwen3.8-27b', credits: 0, inputTokens: 900, outputTokens: 300,
  createdAt: at, updatedAt: at,
  content: {
    language: 'ko', partial: [], strict: true,
    sources: [{ n: 1, fileId: 'f1', name: '02강 시간과 시계.pdf', kind: 'file', chars: 900, read: 900 }],
    questions: [
      { id: 'a', type: 'mc', question: '벡터 시계로 판단할 수 있는 것은?', choices: ['물리 시각', '동시성', '지연', '대역폭'],
        answer: 1, explanation: '벡터 시계는 동시성을 판단합니다 [1].', cite: [1] },
      { id: 'b', type: 'short', question: '같은 프로세스에서 뒤 사건의 램포트 값은?', answer: '더 큰 값',
        explanation: '뒤의 사건은 더 큰 값을 받습니다 [1].', cite: [1] },
    ],
  },
}

async function fixture(page: Page, info: TestInfo, opts: { strict?: boolean } = {}) {
  const origin = new URL(String(info.project.use.baseURL)).origin
  const unexpected: string[] = []
  const writes: { method: string; path: string; data: unknown }[] = []
  let off: string[] = []
  let studio: Record<string, unknown>[] = []
  let notes: Record<string, unknown>[] = [
    { id: 'n1', projectId, title: '시험 범위', body: '6~10강', origin: 'manual', sessionId: null, messageId: null,
      fileId: null, createdAt: at, updatedAt: at },
  ]
  let studioPolls = 0
  const project = {
    id: projectId, name: '분산 시스템', description: '', emoji: '📁', instructions: '', skillIds: [],
    designSystemId: null, renderTemplates: {}, sessionIds: [sessionId], createdAt: at, updatedAt: at,
    files: SOURCES.map(({ kind: _k, digest: _d, guideState: _g, readable: _r, ...file }) => file),
  }
  const session = {
    id: sessionId, title: '시계 질문', kind: 'chat', model: 'strict-local/qwen3.8-27b', routingMode: 'manual',
    projectId, agentId: null, artifactId: null, pending: null, renderTemplateId: null, pinned: false,
    createdAt: at, updatedAt: at, preview: null, messageCount: 2, made: null,
    messages: [
      { id: 'm1', role: 'user', content: '벡터 시계와 래프트를 설명해 줘', createdAt: at },
      { id: 'm2', role: 'assistant', content: ANSWER, model: 'strict-local/qwen3.8-27b', createdAt: at },
    ],
  }
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    const body = () => (request.postData() ? request.postDataJSON() : null)
    if (path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600, user })
    if (method === 'GET' && path === '/auth/config') {
      return json({ brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report'],
        privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false })
    }
    if (method === 'GET' && path === '/models') return json({ models: [], defaultChatModel: '',
      litellmAvailable: false, autoRouting: { enabled: false, available: false } })
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (method === 'GET' && path === '/projects') return json([project])
    if (method === 'GET' && path === `/projects/${projectId}`) return json(project)
    if (method === 'GET' && path === '/sessions') return json([{ ...session, messages: null }])
    if (method === 'GET' && path === `/sessions/${sessionId}`) return json(session)
    if (method === 'GET' && path === `/projects/${projectId}/shelf`) {
      return json({ projectId, sources: SOURCES, strictLocal: opts.strict ?? true, kinds: [] })
    }
    if (path === `/sessions/${sessionId}/sources`) {
      if (method === 'PUT') {
        writes.push({ method, path, data: body() })
        off = body().off
      }
      return json({ off })
    }
    if (method === 'GET' && path === `/projects/${projectId}/shelf/guide/f1`) {
      return json({ state: 'done', summary: '논리 시계로 사건 순서를 정하는 법.', topics: ['램포트 시계', '벡터 시계'],
        questions: ['벡터 시계로 동시성을 어떻게 판단하나요?'], model: 'strict-local/qwen3.8-flash' })
    }
    if (method === 'GET' && path.startsWith(`/projects/${projectId}/shelf/guide/`)) {
      return json({ state: 'none', summary: '', topics: [], questions: [], model: '' })
    }
    if (method === 'GET' && path === `/projects/${projectId}/shelf/locate`) {
      writes.push({ method, path, data: Object.fromEntries(url.searchParams) })
      const text = '[페이지 2]\n벡터 시계는 프로세스 수만큼의 칸을 가진다. 벡터 시계로는 두 사건이 동시에 일어났는지 판단할 수 있다.'
      const focus = text.indexOf('벡터 시계로는')
      return json({ fileId: 'f1', name: '02강 시간과 시계.pdf', mime: 'text/plain', kind: 'file', sourceUrl: null,
        chars: text.length, windowStart: 0, window: text, start: 0, end: text.length, focusStart: focus,
        focusEnd: text.length, page: 2, seconds: null, score: 0.8, matched: true })
    }
    if (method === 'GET' && path === `/projects/${projectId}/studio`) {
      studioPolls += 1
      if (studioPolls > 1) studio = studio.map((row) => (row.state === 'running' ? QUIZ : row))
      return json(studio)
    }
    if (method === 'POST' && path === `/projects/${projectId}/studio/estimate`) {
      writes.push({ method, path, data: body() })
      return json({ kind: body().kind, sources: body().sourceIds.length, partial: [], model: 'strict-local/qwen3.8-27b',
        modelLabel: 'Qwen 27B', strict: true, required: true, inputTokens: 1200, outputTokens: 2700, credits: 0, maxCredits: 0 })
    }
    if (method === 'POST' && path === `/projects/${projectId}/studio`) {
      writes.push({ method, path, data: body() })
      studio = [{ ...QUIZ, state: 'running', content: null }]
      return json(studio[0], 202)
    }
    if (method === 'GET' && path === `/projects/${projectId}/notes`) return json(notes)
    if (method === 'POST' && path === `/projects/${projectId}/notes`) {
      writes.push({ method, path, data: body() })
      const made = { id: `n${notes.length + 1}`, projectId, title: body().title || '메모', body: body().body,
        origin: body().origin, sessionId: body().sessionId ?? null, messageId: body().messageId ?? null,
        fileId: null, createdAt: at, updatedAt: at }
      notes = [made, ...notes]
      return json(made, 201)
    }
    if (method === 'PATCH' && path === '/notes/n1') {
      writes.push({ method, path, data: body() })
      return json({ detail: 'note_conflict' }, 409)
    }
    if (method === 'POST' && path === '/notes/n1/source') {
      writes.push({ method, path, data: null })
      notes = notes.map((n) => (n.id === 'n1' ? { ...n, fileId: 'f9' } : n))
      return json({ ...SOURCES[2], id: 'f9', name: '시험 범위.md' })
    }
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && (path.endsWith('/jobs') || ['/agents', '/skills', '/memory', '/tools', '/connectors',
      '/connectors/catalog', '/designs', '/design-templates', '/shares', '/artifacts'].includes(path))) {
      return json([])
    }
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'Unmocked fixture request' }, 501)
  })
  return { unexpected, writes }
}

const shot = async (page: Page, name: string, info: TestInfo) => {
  if (!shots) return
  const width = info.project.use.viewport?.width ?? 1440
  await page.screenshot({ path: `${shots}/${name}-${width}.png`, fullPage: false })
}

test('a conversation keeps its own source switches, and a citation opens the cited passage', async ({ page }, info) => {
  const state = await fixture(page, info)
  await page.goto(`/s/${sessionId}`)
  await page.getByRole('button', { name: '자료', exact: true }).click()
  const panel = page.getByTestId('sources-panel')
  await expect(panel.getByTestId('sources-count')).toHaveText('3/3개 사용')
  await expect(panel.getByText('이 자료는 내부 자료라 strict-local 모델만 읽습니다.')).toBeVisible()

  await panel.getByLabel('05강 래프트.mp4 사용').uncheck()
  await expect(panel.getByTestId('sources-count')).toHaveText('2/3개 사용')
  await expect.poll(() => state.writes.filter((w) => w.method === 'PUT').at(-1)?.data).toEqual({ off: ['f2'] })

  await panel.getByRole('button', { name: '모두 해제' }).click()
  await expect(panel.getByRole('status')).toContainText('자료를 모두 껐습니다')
  await panel.getByRole('button', { name: '모두 선택' }).click()
  await expect(panel.getByTestId('sources-count')).toHaveText('3/3개 사용')

  // a source's guide: a suggested question goes into the composer, not sent
  await panel.getByRole('button', { name: /02강 시간과 시계\.pdf/ }).click()
  await panel.getByRole('button', { name: '벡터 시계로 동시성을 어떻게 판단하나요?' }).click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('벡터 시계로 동시성을 어떻게 판단하나요?')
  await shot(page, 'sources-panel', info)

  // the answer's 「[1]」 opens the viewer on the cited sentence
  if (await panel.isVisible()) await panel.getByRole('button', { name: '닫기' }).click()
  await page.getByRole('button', { name: '출처 1 원문 보기: 02강 시간과 시계.pdf' }).click()
  const viewer = page.getByTestId('source-viewer')
  await expect(viewer).toBeVisible()
  await expect(viewer.getByTestId('source-highlight')).toContainText('벡터 시계로는 두 사건이 동시에 일어났는지')
  await expect(viewer).toContainText('2쪽')
  const asked = state.writes.find((w) => w.path.endsWith('/shelf/locate'))?.data as Record<string, string>
  expect(asked).toMatchObject({ name: '02강 시간과 시계.pdf', page: '2' })
  expect(asked.q).toContain('동시에 일어났는지 판단할 수 있습니다')
  await shot(page, 'source-viewer', info)
  expect(state.unexpected).toEqual([])
})

test('an answer is pinned to the notes from the conversation', async ({ page }, info) => {
  const state = await fixture(page, info)
  await page.goto(`/s/${sessionId}`)
  await page.getByRole('button', { name: '메모함에 저장' }).first().click()
  await expect.poll(() => state.writes.find((w) => w.path.endsWith('/notes'))?.data).toMatchObject({
    origin: 'answer', sessionId, messageId: 'm2' })
  expect(state.unexpected).toEqual([])
})

test('the studio shows the estimate first, then a quiz is played and the wrong ones retried', async ({ page }, info) => {
  const state = await fixture(page, info)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: '스튜디오' }).click()
  const picker = page.getByTestId('source-picker')
  await expect(picker).toContainText('3/3개 선택')
  await picker.getByLabel('회의 메모.txt').uncheck()
  await shot(page, 'studio', info)
  await page.locator('[data-studio-kind="quiz"]').click()
  const estimate = page.getByTestId('studio-estimate')
  await expect(estimate).toContainText('자료 2개')
  await expect(estimate).toContainText('크레딧 없음')
  await expect(estimate).toContainText('strict-local')
  await page.getByRole('dialog').getByRole('button', { name: '만들기' }).click()
  const started = state.writes.find((w) => w.method === 'POST' && w.path.endsWith('/studio'))?.data as Record<string, unknown>
  expect(started).toMatchObject({ kind: 'quiz', sourceIds: ['f1', 'f2'], options: { count: 10 } })

  // running, then done on the next poll
  const row = page.locator('[data-studio-item="q1"]')
  await expect(row).toHaveAttribute('data-state', 'done', { timeout: 15_000 })
  await row.getByRole('button', { name: /^시계와 합의 퀴즈 퀴즈/ }).click()
  const player = page.getByRole('dialog', { name: '시계와 합의 퀴즈' })
  await player.getByRole('button', { name: /물리 시각/ }).click()
  await expect(player.getByTestId('quiz-feedback')).toContainText('틀렸습니다')
  await expect(player.getByTestId('quiz-feedback')).toContainText('벡터 시계는 동시성을 판단합니다')
  await shot(page, 'quiz', info)
  await player.getByRole('button', { name: '다음' }).click()
  await player.getByLabel('답').fill('더 큰 값')
  await player.getByRole('button', { name: '확인' }).click()
  await expect(player.getByTestId('quiz-feedback')).toContainText('맞았습니다')
  await player.getByRole('button', { name: '결과 보기' }).click()
  await expect(player.getByTestId('quiz-result')).toContainText('1/2 맞혔습니다')
  await player.getByRole('button', { name: '틀린 문제만 다시' }).click()
  await expect(player).toContainText('벡터 시계로 판단할 수 있는 것은?')
  await expect(player.getByTestId('quiz-score')).toContainText('맞힘 0/1')
  expect(state.unexpected).toEqual([])
})

test('a note edit that lost the race offers both versions, and a note becomes a source', async ({ page }, info) => {
  const state = await fixture(page, info)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: '메모함' }).click()
  const note = page.locator('[data-note="n1"]')
  await note.getByRole('button', { name: '고치기' }).click()
  const editor = page.getByRole('dialog', { name: '메모 고치기' })
  await editor.getByLabel('내용').fill('6~10강, 비잔틴 포함')
  await expect(editor.getByText('저장 안 됨')).toBeVisible()
  await editor.getByRole('button', { name: '저장' }).click()
  await expect(editor.getByRole('alert')).toContainText('다른 곳에서 이 메모를 먼저 저장했습니다')
  await expect(editor.getByRole('button', { name: '내 편집 내려받기' })).toBeVisible()
  expect(state.writes.find((w) => w.method === 'PATCH')?.data).toMatchObject({ expected: at })
  // closing with the edit unsaved asks first
  await editor.getByRole('button', { name: '닫기' }).first().click()
  await expect(editor.getByRole('alertdialog')).toContainText('저장하지 않은 편집이 있습니다.')
  await editor.getByRole('alertdialog').getByRole('button', { name: '버리기' }).click()
  await expect(editor).toHaveCount(0)
  await shot(page, 'notes', info)
  await note.getByRole('button', { name: '자료로 만들기' }).click()
  await expect(note).toContainText('자료로 쓰는 중')
  expect(state.unexpected).toEqual([])
})

test('the studio makes text only: no audio overview is offered', async ({ page }, info) => {
  const state = await fixture(page, info)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: '스튜디오' }).click()
  await expect(page.locator('[data-studio-kind="flashcards"]')).toBeVisible()
  await expect(page.locator('[data-studio-kind="audio"]')).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})

test('@phone the sources panel covers the conversation on a phone', async ({ page }, info) => {
  const state = await fixture(page, info)
  await page.goto(`/s/${sessionId}`)
  await page.getByRole('button', { name: '자료', exact: true }).click()
  const panel = page.getByTestId('sources-panel')
  const box = await panel.boundingBox()
  expect(box?.width ?? 0).toBeGreaterThan(300)
  await shot(page, 'sources-panel', info)
  await panel.getByRole('button', { name: '닫기' }).click()
  await expect(panel).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})
