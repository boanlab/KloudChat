import { expect, test, type Page } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * A strict-local turn that needs the web stops at a card listing the queries that would leave;
 * the person allows, edits, or answers from the material. Every API response is a fixture.
 */

const sessionId = 'search-consent'
const now = '2026-11-13T00:00:00.000Z'
const question = '기존 연구들에서는 이런 기술을 쓴 논문이 없나?'
const pause = '이 질문에 답하려면 외부 검색이 필요합니다. 아래 검색어만 밖으로 나갑니다. 프로젝트 자료 내용은 보내지 않습니다.'
const model = 'strict-local/qwen3.8-flash'
const shots = process.env.SEARCH_CONSENT_SCREENSHOT_DIR

const strictRouting = {
  requestedModels: ['local/qwen3.8-flash'], routedModels: [model], effectiveModels: [model],
  actualModels: [], action: 'internal_strict_local', dataBoundary: 'self_hosted',
}
const card = {
  status: 'pending',
  askedAt: now,
  queries: [
    { text: 'federated learning batch normalization medical segmentation', masked: 0 },
    { text: '연합학습 의료영상 분할 선행연구', masked: 0 },
    { text: '본 연구는 연합학습 기반 의료영상 분할에서 클라이언트별 배치 정규화 통계를', masked: 0,
      blocked: 'material', reason: '자료의 문장을 그대로 담고 있어 보내지 않습니다.' },
  ],
}
const questionRow = { id: 'q1', role: 'user', content: question, attachments: [], createdAt: now,
  routing: { ...strictRouting, turnOptions: { webSearch: 'auto' } } }
const pausedRow = {
  id: 'a1', role: 'assistant', content: pause, attachments: [], model, createdAt: now,
  routing: { ...strictRouting, searchConsent: card },
  usage: { inputTokens: 40, outputTokens: 12, credits: 0 },
}

type Answer = 'allow' | 'deny'

function answerRow(kind: Answer, sent: string[]) {
  const searchConsent = kind === 'allow'
    ? { status: 'allowed', source: 'card', queries: sent, searches: sent.length, withheld: [],
        decidedBy: 'fixture-user', decidedAt: now, remember: false }
    : { status: 'declined', source: 'card', queries: [], searches: 0, withheld: [],
        decidedBy: 'fixture-user', decidedAt: now, remember: false }
  const content = kind === 'allow'
    ? 'FedBN(Li et al., 2021)이 클라이언트별 배치 정규화를 분리합니다 [1].\n\n### 출처\n1. [FedBN](https://arxiv.org/abs/2102.07623)'
    : '자료의 관련 연구 절에는 FedAvg와 FedProx만 나옵니다.\n\n_외부 검색 없이 자료만으로 답했습니다._'
  return {
    id: 'a2', role: 'assistant', content, attachments: [], model, createdAt: now,
    routing: { ...strictRouting, searchConsent },
    usage: { inputTokens: 900, outputTokens: 80, credits: 0 },
  }
}

async function mock(page: Page, { saved = true }: { saved?: boolean } = {}) {
  const posts: Record<string, unknown>[] = []
  const unexpected: string[] = []
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  const row = {
    id: sessionId, title: '선행 연구 확인', kind: 'chat', model, routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pinned: false,
    messages: saved ? [questionRow, pausedRow] : [] as unknown[],
    messageCount: saved ? 2 : 0, createdAt: now, updatedAt: now,
  }
  const sse = (events: unknown[]) => ({
    status: 200,
    headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
    body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'fixture-user', email: 'fixture@example.test', name: '연구자', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
        allowedModels: [], createdAt: now,
        preferences: { autoMemory: false, showUsage: true, streamResponses: true },
      },
    })
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: true, strictLocalWebSearch: 'ask' },
      passwordResetEnabled: false, dictationEnabled: false,
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: model,
      models: [{
        id: model, label: 'qwen3.8-flash (strict-local)', name: 'qwen3.8-flash', vendor: 'Self-hosted',
        provider: 'local', kinds: ['chat'], modality: 'chat', dataBoundary: 'self_hosted',
        strictLocal: true, privacyOnly: false, creditCost: 0, inputCreditCost: 0,
        supportsTools: true, supportsVision: false, contextWindow: 64000,
      }],
    })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && request.method() === 'GET') {
      const { messages: _messages, ...summary } = row
      return json([summary])
    }
    if (path === `/sessions/${sessionId}` && request.method() === 'GET') return json(row)
    if (path === `/sessions/${sessionId}/messages` && request.method() === 'GET') return json(row.messages)
    if (path === `/sessions/${sessionId}/messages` && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>
      posts.push(body)
      const consent = body.searchConsent as { decision: Answer; queries?: string[] } | undefined
      if (!consent) {
        // the first turn: the model asked, the server paused
        row.messages = [questionRow, pausedRow]
        row.messageCount = 2
        return route.fulfill(sse([
          { type: 'privacy_route', ...strictRouting },
          { type: 'delta', text: pause },
          { type: 'privacy_route', ...strictRouting, searchConsent: card },
          { type: 'usage', inputTokens: 40, outputTokens: 12, credits: 0 },
          { type: 'done', messageId: 'a1' },
        ]))
      }
      const sent = consent.queries ?? card.queries.filter((q) => !q.blocked).map((q) => q.text)
      const answer = answerRow(consent.decision, sent)
      row.messages = [questionRow, answer]
      return route.fulfill(sse([
        { type: 'privacy_route', ...strictRouting },
        ...(consent.decision === 'allow'
          ? [{ type: 'step', id: 'h0_0', label: '외부 검색 요청', status: 'done', detail: `검색어만 전송 · ${sent.join(' · ')}` }]
          : []),
        { type: 'delta', text: answer.content },
        { type: 'privacy_route', ...answer.routing },
        { type: 'usage', ...answer.usage },
        { type: 'done', messageId: 'a2' },
      ]))
    }
    if (path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    const shell = shellRead(request.method(), path)
    if (shell) return route.fulfill(shell)
    if (request.method() === 'GET' && [
      '/projects', '/artifacts', '/skills', '/memory', '/agents', '/tools', '/connectors',
      '/connectors/catalog', '/jobs', '/designs', '/design-templates', '/shares',
      `/sessions/${sessionId}/jobs`,
    ].includes(path)) return json([])
    unexpected.push(`${request.method()} ${path}`)
    return route.fulfill({ status: 501, json: { detail: 'Unmocked fixture request' } })
  })
  return { posts, unexpected }
}

const consentCard = (page: Page) => page.getByTestId('search-consent')

async function badge(page: Page, text: string) {
  const details = page.getByRole('button', { name: /처리 내역 \d+건/ })
  if (await details.isVisible()) await details.click()
  await expect(page.getByTestId('request-protection').last()).toHaveText(text)
}

async function shot(page: Page, name: string) {
  if (!shots) return
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: `${shots}/${name}.png`, animations: 'disabled', fullPage: false })
}

test('카드는 나갈 검색어만 보여 주고, 자료 문장이 든 검색어는 이유와 함께 막는다', async ({ page }) => {
  const state = await mock(page)
  await page.goto(`/s/${sessionId}`)
  const shown = consentCard(page)
  await expect(shown).toBeVisible()
  await expect(shown.getByText(pause)).toBeVisible()
  await expect(shown.getByText('연합학습 의료영상 분할 선행연구')).toBeVisible()
  await expect(shown.getByText('자료의 문장을 그대로 담고 있어 보내지 않습니다.')).toBeVisible()
  // the paused sentence is the card's, not repeated as an answer
  await expect(page.getByText(pause)).toHaveCount(1)
  await badge(page, 'strict-local로 보호됨 · 외부 검색 확인 대기')
  await shot(page, 'card-desktop')
  // a reload while it waits: the card is on the stored answer
  await page.reload()
  await expect(consentCard(page)).toBeVisible()
  await expect(consentCard(page).getByRole('button', { name: '허용', exact: true })).toBeEnabled()
  expect(state.posts).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('허용: 같은 질문을 다시 묻고 카드의 검색어만 보낸 뒤 배지가 그 횟수를 말한다', async ({ page }) => {
  const state = await mock(page)
  await page.goto(`/s/${sessionId}`)
  await consentCard(page).getByRole('button', { name: '허용', exact: true }).click()
  await expect(page.getByText(/FedBN\(Li et al\., 2021\)/)).toBeVisible()
  await expect(consentCard(page)).toHaveCount(0)
  expect(state.posts).toHaveLength(1)
  expect(state.posts[0]).toMatchObject({
    retryOf: 'q1',
    searchConsent: { messageId: 'a1', decision: 'allow' },
  })
  expect((state.posts[0].searchConsent as Record<string, unknown>).queries).toBeUndefined()
  await badge(page, 'strict-local로 보호됨 · 외부 검색 2회(허용)')
  await shot(page, 'allowed-desktop')
  expect(state.unexpected).toEqual([])
})

test('검색어 고치고 허용: 고친 검색어를 보내고, 이 대화에서는 계속 허용을 함께 보낸다', async ({ page }) => {
  const state = await mock(page)
  await page.goto(`/s/${sessionId}`)
  const shown = consentCard(page)
  await shown.getByLabel('이 대화에서는 계속 허용').check()
  await shown.getByRole('button', { name: '검색어 고치고 허용' }).click()
  // the withheld one opens empty: it is not sent as it was
  await expect(shown.getByLabel('검색어 3')).toHaveValue('')
  await shown.getByRole('button', { name: '검색어 빼기' }).last().click()
  await shown.getByLabel('검색어 2').fill('FedBN non-IID')
  await shot(page, 'editing-desktop')
  await shown.getByRole('button', { name: '고친 검색어로 허용' }).click()
  await expect(page.getByText(/FedBN\(Li et al\., 2021\)/)).toBeVisible()
  expect(state.posts[0]).toMatchObject({
    retryOf: 'q1',
    searchConsent: {
      messageId: 'a1', decision: 'allow', remember: true,
      queries: ['federated learning batch normalization medical segmentation', 'FedBN non-IID'],
    },
  })
  expect(state.unexpected).toEqual([])
})

test('이번엔 자료만으로: 아무것도 보내지 않고 답이 그렇다고 말한다', async ({ page }) => {
  const state = await mock(page)
  await page.goto(`/s/${sessionId}`)
  await consentCard(page).getByRole('button', { name: '이번엔 자료만으로' }).click()
  await expect(page.getByText('외부 검색 없이 자료만으로 답했습니다.')).toBeVisible()
  expect(state.posts[0]).toMatchObject({ retryOf: 'q1', searchConsent: { messageId: 'a1', decision: 'deny' } })
  await badge(page, 'strict-local로 보호됨 · 외부 검색 안 함')
  await shot(page, 'declined-desktop')
  expect(state.unexpected).toEqual([])
})

test('스트리밍으로 받은 카드도 바로 답할 수 있다(질문 id는 저장본에서 읽는다)', async ({ page }) => {
  const state = await mock(page, { saved: false })
  await page.goto(`/s/${sessionId}`)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill(question)
  await input.press('Enter')
  await expect(consentCard(page)).toBeVisible()
  await consentCard(page).getByRole('button', { name: '허용', exact: true }).click()
  await expect(page.getByText(/FedBN\(Li et al\., 2021\)/)).toBeVisible()
  expect(state.posts.map((post) => post.retryOf ?? null)).toEqual([null, 'q1'])
  expect(state.unexpected).toEqual([])
})

test('예전 답의 배지는 strict-local 이동을 외부 전송이라고 하지 않는다', async ({ page }) => {
  const state = await mock(page)
  await page.route(`**/api/sessions/${sessionId}`, (route) => route.fulfill({ json: {
    id: sessionId, title: '선행 연구 확인', kind: 'chat', model, routingMode: 'manual', projectId: null,
    agentId: null, artifactId: null, pinned: false, messageCount: 2, createdAt: now, updatedAt: now,
    messages: [questionRow, { ...pausedRow, content: '관련 연구 절에 따르면 …', routing: strictRouting }],
  } }))
  await page.goto(`/s/${sessionId}`)
  await badge(page, 'strict-local로 보호됨')
  await expect(page.getByText('확인 후 요청 원문은 외부 전송')).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})

test('폰 너비에서도 카드와 버튼이 화면 안에 있다 @phone', async ({ page }) => {
  const state = await mock(page)
  await page.goto(`/s/${sessionId}`)
  const shown = consentCard(page)
  await expect(shown).toBeVisible()
  for (const name of ['허용', '검색어 고치고 허용', '이번엔 자료만으로']) {
    const button = shown.getByRole('button', { name, exact: true })
    await button.scrollIntoViewIfNeeded()
    const box = (await button.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  await shot(page, 'card-phone')
  await shown.getByRole('button', { name: '허용', exact: true }).click()
  await expect(page.getByText(/FedBN\(Li et al\., 2021\)/)).toBeVisible()
  await shot(page, 'allowed-phone')
  expect(state.unexpected).toEqual([])
})
