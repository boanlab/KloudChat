import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

test.use({ serviceWorkers: 'block' })

/** How a finished turn reads from the server's stored rows: an answer that broke off is marked
 *  as such, and the files a code step wrote sit under the answer that talks about them.
 *  No backend: every call is answered here. */

const at = '2026-10-11T00:00:00.000Z'
const id = '44444444444444444444444444444444'

const user = {
  id: 'fixture-user', name: 'Outcome fixture', email: 'fixture@example.test', role: 'user',
  status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
  createdAt: at, preferences: { autoMemory: false, showUsage: true, streamResponses: true },
}

type Row = Record<string, unknown>

async function sessionFixture(page: Page, testInfo: TestInfo, messages: Row[], extra: Row = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const session = {
    id, title: '실험 데이터 분석', kind: 'chat', model: 'fixture/model', routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pinned: false, messages,
    messageCount: messages.length, made: null, createdAt: at, updatedAt: at, ...extra,
  }
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'POST' && path === '/auth/refresh') {
      return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600, user } })
    }
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: {
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'slides'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: {
      models: [{ id: 'fixture/model', label: 'Fixture', name: 'Fixture', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat', 'report', 'slides'], modality: 'chat',
        dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, supportsTools: true,
        contextWindow: 64000 }],
      defaultChatModel: 'fixture/model',
      defaults: { chat: 'fixture/model', report: 'fixture/model', slides: 'fixture/model' },
      litellmAvailable: false, autoRouting: { enabled: false, available: false },
    } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: {
      monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000,
    } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [session] })
    if (method === 'GET' && path === `/sessions/${id}`) return route.fulfill({ json: session })
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET') return route.fulfill({ json: [] })
    return route.abort('blockedbyclient')
  })
  return session
}

const question = { id: 'm1', role: 'user', content: '실험 데이터를 분석해 줘.', createdAt: at }

test('끊긴 답은 끊겼다고 붙이고, 아래 오류와 함께 보인다', async ({ page }, testInfo) => {
  await sessionFixture(page, testInfo, [question, {
    id: 'm2', role: 'assistant', content: '분석을 마쳤고 차트를 문서에 넣었습니다.', createdAt: at,
    model: 'fixture/model', failure: 'interrupted', steps: [],
  }])
  await page.goto(`/s/${id}`)
  const partial = page.getByTestId('partial-answer')
  await expect(partial.getByText('끊긴 답 · 끝까지 쓰지 못했습니다')).toBeVisible()
  await expect(partial.getByText('분석을 마쳤고 차트를 문서에 넣었습니다.')).toBeVisible()
  await expect(page.getByText('답변이 중간에 끊겨 여기까지만 남았습니다.')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('partial-answer.png') })
})

test('다 쓴 답에는 끊김 표시가 없다', async ({ page }, testInfo) => {
  await sessionFixture(page, testInfo, [question, {
    id: 'm2', role: 'assistant', content: '분석을 마쳤습니다.', createdAt: at, model: 'fixture/model', steps: [],
  }])
  await page.goto(`/s/${id}`)
  await expect(page.getByText('분석을 마쳤습니다.')).toBeVisible()
  await expect(page.getByTestId('partial-answer')).toHaveCount(0)
})

test('코드가 만든 파일은 접힌 진행 기록이 아니라 답 아래에서 내려받는다', async ({ page }, testInfo) => {
  await sessionFixture(page, testInfo, [question, {
    id: 'm2', role: 'assistant', content: '집단별 산점도를 그렸습니다.', createdAt: at, model: 'fixture/model',
    steps: [{
      id: 'h1', type: 'tool', label: '코드 실행', status: 'done',
      outputs: [{ id: 'f1', name: 'scatter.png', size: 48213 }],
    }],
  }])
  await page.goto(`/s/${id}`)
  const made = page.getByTestId('made-files')
  await expect(made.getByRole('button', { name: /scatter\.png/ })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('made-files.png') })
})

test('슬라이드 개요 카드는 배치를 사람 말로 부른다', async ({ page }, testInfo) => {
  await sessionFixture(page, testInfo, [{ ...question, content: '세미나 발표 자료를 만들어 줘.' }], {
    kind: 'slides',
    pending: {
      stage: 'outline', request: '세미나 발표 자료를 만들어 줘.', attachments: [], answers: {},
      plan: { title: '세미나', slides: [
        { title: '표지', layout: 'title' },
        { title: '배경', layout: 'bands' },
        { title: '세 가지 기여', layout: 'cards-3' },
      ] },
    },
  })
  await page.goto(`/s/${id}`)
  await expect(page.getByLabel('3번 제목')).toHaveValue('세 가지 기여')
  for (const name of ['표지', '항목과 설명', '카드 3개']) {
    await expect(page.getByText(name, { exact: true }).last()).toBeVisible()
  }
  for (const raw of ['bands', 'cards-3']) await expect(page.getByText(raw, { exact: true })).toHaveCount(0)
})
