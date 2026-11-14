import { expect, test } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * A finished web report edited from the chat in place: the turn names the report open beside
 * the chat, the document tools save a new version mid-turn, the panel shows it at once and the
 * reply reads as a chat's. No backend: every call is answered here.
 */

test.use({ serviceWorkers: 'block' })

const at = '2026-11-11T07:00:00.000Z'
const sessionId = 'a6666666666666666666666666666666'
const artifactId = 'b6666666666666666666666666666666'
const request = '사내 클라우드 보안 점검 결과 보고서'
const headings = ['점검 개요', '발견 사항']

function sse(events: unknown[]) {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
}

const user = {
  id: 'fixture-user', name: 'Follow fixture', email: 'fixture@example.test', role: 'user',
  status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
  allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}
const model = {
  id: 'fixture/base', label: 'Fixture', name: 'Fixture', vendor: 'Fixture', provider: 'fixture',
  kinds: ['chat', 'report', 'slides'], modality: 'chat', dataBoundary: 'external', creditCost: 1,
  inputCreditCost: 1, supportsTools: true, contextWindow: 64000,
}
const emptyLists = [
  '/artifacts', '/projects', '/skills', '/memory', '/tools', '/connectors', '/connectors/catalog',
  '/jobs', '/designs', '/shares', '/design-templates', '/skills/store', '/agents',
  '/user-pipelines',
]

test('an edit of the open report is saved in place and shows at once', async ({ page }, testInfo) => {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const sent: Record<string, unknown>[] = []
  const question = { id: 'm1', role: 'user', content: request, createdAt: at }
  const made = { id: 'm2', role: 'assistant', content: '보고서를 만들었습니다.', createdAt: at,
    artifactIds: [artifactId] }
  const section = (text: string) => headings.map((heading, i) => (
    { id: `s${i}`, heading, level: 1, status: 'done', content: i === 0 ? text : '발견 본문.' }))
  let artifact = {
    id: artifactId, title: '보안 점검 결과', kind: 'report', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { kind: 'report', title: '보안 점검 결과', sources: [],
            sections: section('예산은 3억 원입니다.') },
  }
  const session = {
    id: sessionId, kind: 'report', title: '보안 점검', projectId: null, agentId: null,
    model: 'fixture/base', routingMode: 'manual', artifactId, skillIds: [],
    renderTemplateId: null, pinned: false, createdAt: at, updatedAt: at, preview: null,
    messageCount: 2, made: null, pending: null, messages: [question, made],
  }

  const notTheApp = /^(?!wss?:\/\/[^/]+\/\?token=).*$/
  await page.context().routeWebSocket(notTheApp, (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = req.method()
    if (method === 'POST' && path === '/auth/refresh') {
      return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600, user } })
    }
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: {
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'slides'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: {
      models: [model], defaultChatModel: 'fixture/base', litellmAvailable: false,
      autoRouting: { enabled: false, available: false },
    } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: {
      monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000,
    } })
    if (method === 'GET' && path === '/sessions') {
      return route.fulfill({ json: [{ ...session, messages: null }] })
    }
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'POST' && path === `/sessions/${sessionId}/messages`) {
      sent.push(req.postDataJSON())
      // the document tools save v2 while the turn runs
      artifact = { ...artifact, version: 2,
                   data: { ...artifact.data, sections: section('예산은 4억 원입니다.') } }
      const reply = '1절의 예산을 4억 원으로 고쳤습니다.'
      Object.assign(session, { messageCount: 4, messages: [question, made,
        { id: 'm3', role: 'user', content: '1절 예산을 4억으로 고쳐 줘', createdAt: at },
        { id: 'm4', role: 'assistant', content: reply, createdAt: at }] })
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse([
        { type: 'step', id: 'h1_0', label: '문서 확인', status: 'done' },
        { type: 'step', id: 'h2_0', label: '문서 편집', status: 'done', detail: '1건', artifactId,
          artifactVersion: 2 },
        { type: 'delta', text: reply },
        { type: 'usage', inputTokens: 10, outputTokens: 5, credits: 1 },
        { type: 'done' },
      ]) })
    }
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return route.fulfill({ json: artifact })
    if (method === 'GET' && path === '/artifacts/counts') {
      return route.fulfill({ json: { counts: {}, total: 0 } })
    }
    if (method === 'GET' && (path.endsWith('/messages') || path.endsWith('/jobs') || path.endsWith('/versions'))) {
      return route.fulfill({ json: [] })
    }
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && emptyLists.includes(path)) return route.fulfill({ json: [] })
    if (method === 'GET' && path.startsWith('/design-templates/')) {
      return route.fulfill({ status: 404, json: { detail: 'not_found' } })
    }
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })

  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel.getByText('예산은 3억 원입니다.')).toBeVisible({ timeout: 20_000 })
  await page.getByRole('textbox', { name: '프롬프트 입력' }).first().fill('1절 예산을 4억으로 고쳐 줘')
  await page.keyboard.press('Enter')
  await expect(panel.getByText('예산은 4억 원입니다.')).toBeVisible()
  await expect(page.getByText('1절의 예산을 4억 원으로 고쳤습니다.')).toBeVisible()
  expect(sent[0].openArtifact).toEqual({ id: artifactId, version: 1 })
  expect(unexpected).toEqual([])
})
