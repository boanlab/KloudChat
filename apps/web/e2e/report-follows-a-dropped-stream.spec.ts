import { expect, test } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * A report whose response drops while the server goes on writing it: the screen says it is
 * still being written, keeps the stop button, and opens the finished report once the
 * transcript says the turn ended. No backend: every call is answered here.
 */

test.use({ serviceWorkers: 'block' })

const at = '2026-11-11T07:00:00.000Z'
const sessionId = 'a5555555555555555555555555555555'
const artifactId = 'b5555555555555555555555555555555'
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

test('a report whose stream dropped is followed until the server has written it', async (
  { page }, testInfo,
) => {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const question = { id: 'm1', role: 'user', content: request, createdAt: at }
  const outline = {
    id: 'm2', role: 'assistant', content: '이렇게 구성하려고 합니다. 확인해 주세요.', createdAt: at,
  }
  const approval = { id: 'm3', role: 'user', content: '이대로 생성해 주세요', createdAt: at }
  const pending = { stage: 'outline', request, attachments: [], answers: {},
    plan: { title: '보안 점검 결과', sections: headings } }
  const session = {
    id: sessionId, kind: 'report', title: '보안 점검', projectId: null, agentId: null,
    model: 'fixture/base', routingMode: 'manual', artifactId: null, skillIds: [],
    renderTemplateId: null, pinned: false, createdAt: at, updatedAt: at, preview: null,
    messageCount: 2, made: null, pending, messages: [question, outline],
  }
  const sections = headings.map((heading, i) => (
    { id: `s${i}`, heading, level: 1, status: 'done', content: `완성된 본문 ${i + 1}.` }))
  const artifact = {
    id: artifactId, title: '보안 점검 결과', kind: 'report', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { kind: 'report', title: '보안 점검 결과', sources: [], sections },
  }
  const written = {
    id: 'm4', role: 'assistant', content: '보고서를 만들었습니다.', createdAt: at,
    artifactIds: [artifactId],
  }
  // Reads of the session after the send: the server is still writing for the first two.
  let reads = -1

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
    if (method === 'GET' && path === `/sessions/${sessionId}`) {
      if (reads < 0) return route.fulfill({ json: session })
      reads += 1
      // The approved outline stays pending while the server writes it.
      const messages = [question, outline, approval]
      if (reads < 3) return route.fulfill({ json: { ...session, messages, messageCount: 3 } })
      return route.fulfill({ json: {
        ...session, pending: null, artifactId, messageCount: 4, messages: [...messages, written],
      } })
    }
    if (method === 'POST' && path === `/sessions/${sessionId}/messages`) {
      reads = 0
      // The response breaks off after the first section: no `usage`, no `error`.
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse([
        { type: 'title', title: '보안 점검 결과' },
        { type: 'section', sectionId: 's0', heading: headings[0], content: '쓰는 중', done: false },
      ]) })
    }
    if (method === 'GET' && path === `/artifacts/${artifactId}`) {
      return route.fulfill({ json: artifact })
    }
    if (method === 'GET' && path === '/artifacts/counts') {
      return route.fulfill({ json: { counts: {}, total: 0 } })
    }
    if (method === 'GET' && (path.endsWith('/messages') || path.endsWith('/jobs'))) {
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

  await page.goto(`/s/${sessionId}`)
  await page.getByRole('button', { name: '이대로 생성' }).click()

  // Still being written, not cut off: the draft stays and the stop button with it.
  await expect(page.getByText('연결이 끊겼지만 서버에서 계속 쓰고 있습니다')).toBeVisible()
  await expect(page.getByText('연결이 끊겨 답변이 중간에 멈췄습니다')).toHaveCount(0)
  await expect(page.getByLabel('중지')).toBeVisible()
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel.getByRole('heading', { name: headings[0] })).toBeVisible()

  // The server finished: its report replaces the draft.
  await expect(panel.getByText('완성된 본문 2.')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('보고서를 만들었습니다.')).toBeVisible()
  await expect(page.getByLabel('중지')).toBeHidden()
  expect(unexpected).toEqual([])
})
