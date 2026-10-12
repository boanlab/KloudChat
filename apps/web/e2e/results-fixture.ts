import { expect, type Page, type TestInfo } from '@playwright/test'
import audit from './fixtures/report-audit.json' with { type: 'json' }
import deckFixture from './fixtures/presentation-deck.json' with { type: 'json' }
import style from './fixtures/report-audit-style.json' with { type: 'json' }
import { shellRead } from './shell-mocks'

/** A report session and a slides session, each with a question card turn and the turn that
 *  wrote the document, against a mocked API that records every write and export. */

export const reportSession = '71717171717171717171717171717171'
export const reportId = '72727272727272727272727272727272'
export const deckSession = '73737373737373737373737373737373'
export const deckId = '74747474747474747474747474747474'
export const shareToken = 'shared-result-token'
const at = '2026-10-08T00:00:00.000Z'
const later = '2026-10-08T00:05:00.000Z'

interface Recorded {
  exports: string[]
  uploads: string[]
  unexpected: string[]
  problems: string[]
}

export const reportTitle = audit.title
export const deckTitle = deckFixture.title

/** The report's sections plus one numeric claim with no citation, so the pre-export check has something to say. */
function reportData() {
  const sections = audit.data.sections.map((section, index) =>
    index === 0 ? { ...section, content: `${section.content}\n\n도입 기업의 매출이 30% 늘었다.` } : section)
  return { ...audit.data, kind: 'report', title: audit.title, sections }
}

function user() {
  return { id: 'fixture-user', name: '결과 카드', email: 'fixture@example.test', role: 'user',
    status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
    createdAt: at, preferences: { autoMemory: false, showUsage: true, streamResponses: true } }
}

function message(id: string, role: 'user' | 'assistant', content: string, extra: Record<string, unknown> = {}) {
  return { id, role, content, steps: null, attachments: null, variants: null, superseded: null,
    usage: role === 'assistant' ? { inputTokens: 1200, outputTokens: 3400, credits: 0 } : null,
    model: role === 'assistant' ? 'fixture/base' : null, routing: null, startedFrom: null, rating: null,
    artifactIds: null, failure: null, createdAt: role === 'user' ? at : later, ...extra }
}

export async function resultsFixture(page: Page, testInfo: TestInfo, options: { reportVersion?: number } = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const recorded: Recorded = { exports: [], uploads: [], unexpected: [], problems: [] }
  page.on('pageerror', (error) => recorded.problems.push(`pageerror: ${error.message}`))
  const report = {
    id: reportId, title: audit.title, kind: 'report', version: options.reportVersion ?? 1, partial: false,
    sessionId: reportSession, projectId: null, createdAt: at, updatedAt: later, data: reportData(),
  }
  const deck = {
    id: deckId, title: deckFixture.title, kind: 'deck', version: 1, partial: false,
    sessionId: deckSession, projectId: null, createdAt: at, updatedAt: later, data: deckFixture.data,
  }
  const session = (id: string, kind: string, title: string, artifactId: string, made: unknown[]) => ({
    id, title, kind, model: 'fixture/base', routingMode: 'manual', projectId: null, agentId: null,
    artifactId, pinned: false, messages: made, messageCount: made.length, made: null,
    createdAt: at, updatedAt: later,
  })
  const reportMessages = [
    message('r-ask', 'user', 'AI 보안 솔루션 기술 동향 보고서 써 줘'),
    message('r-card', 'assistant', '시작하기 전에 확인할 것이 있습니다.', { createdAt: '2026-10-08T00:01:00.000Z' }),
    message('r-done', 'assistant', '5개 섹션으로 보고서를 작성했습니다.', { artifactIds: [reportId], artifactVersion: 1 }),
  ]
  const deckMessages = [
    message('d-ask', 'user', '동아리 행사 신청 흐름 슬라이드 만들어 줘'),
    message('d-done', 'assistant', '4장짜리 슬라이드를 만들었습니다.', { artifactIds: [deckId], artifactVersion: 1 }),
  ]
  const sessions = [
    session(reportSession, 'report', audit.title, reportId, reportMessages),
    session(deckSession, 'slides', deckFixture.title, deckId, deckMessages),
  ]
  const artifacts: Record<string, { title: string; data: unknown; version?: number }> = { [reportId]: report, [deckId]: deck }
  const models = [{ id: 'fixture/base', label: 'Fixture', name: 'Fixture', vendor: 'Fixture', provider: 'fixture',
    kinds: ['chat', 'report', 'slides'], modality: 'chat', dataBoundary: 'external', creditCost: 1,
    inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }]

  await page.context().routeWebSocket('**/*', (socket) => {
    const url = new URL(socket.url())
    // Vite's own development channel is not an application API.
    if (url.origin === origin.replace(/^http/, 'ws')) return socket.connectToServer()
    recorded.unexpected.push('WebSocket')
    socket.close()
  })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'POST' && path === '/auth/refresh') {
      return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600, user: user() } })
    }
    if (method === 'GET' && path === '/auth/config') {
      return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'slides'],
        privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false } })
    }
    if (method === 'GET' && path === '/models') {
      return route.fulfill({ json: { models, defaultChatModel: 'fixture/base', litellmAvailable: false,
        autoRouting: { enabled: false, available: false } } })
    }
    if (method === 'GET' && path === '/credits') {
      return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    }
    if (method === 'GET' && /^\/design-templates\/[a-z_-]+\/style$/.test(path)) return route.fulfill({ json: style })
    if (method === 'GET' && path === '/design-templates') return route.fulfill({ json: audit.templates })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: sessions })
    const one = sessions.find((row) => path === `/sessions/${row.id}`)
    if (method === 'GET' && one) return route.fulfill({ json: one })
    const own = sessions.find((row) => path === `/sessions/${row.id}/messages`)
    if (method === 'GET' && own) return route.fulfill({ json: own.messages })
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: Object.values(artifacts) })
    if (method === 'GET' && path === '/artifacts/counts') {
      return route.fulfill({ json: { counts: { report: 1, deck: 1 }, total: 2 } })
    }
    const exported = path.match(/^\/artifacts\/([0-9a-f]{32})\/export$/)
    if (method === 'GET' && exported) {
      const format = url.searchParams.get('format') ?? ''
      recorded.exports.push(`${exported[1] === reportId ? 'report' : 'deck'}:${format}`)
      return route.fulfill({ status: 200, body: `# ${artifacts[exported[1]]?.title}\n\n본문`,
        headers: { 'content-type': format === 'md' ? 'text/markdown; charset=utf-8' : 'application/octet-stream' } })
    }
    const versions = path.match(/^\/artifacts\/([0-9a-f]{32})\/versions$/)
    if (method === 'GET' && versions) {
      return route.fulfill({ json: [{ version: 1, summary: '처음 작성', createdAt: at }] })
    }
    const version = path.match(/^\/artifacts\/([0-9a-f]{32})\/versions\/(\d+)$/)
    if (method === 'GET' && version) {
      return route.fulfill({ json: { version: Number(version[2]), summary: '처음 작성', createdAt: at,
        data: artifacts[version[1]]?.data } })
    }
    const single = path.match(/^\/artifacts\/([0-9a-f]{32})$/)
    if (method === 'GET' && single && artifacts[single[1]]) return route.fulfill({ json: artifacts[single[1]] })
    if (method === 'PATCH' && single && artifacts[single[1]]) {
      // A panel's save: the next version, as the server answers it.
      const was = artifacts[single[1]]
      const body = request.postDataJSON() as { data?: unknown }
      artifacts[single[1]] = { ...was, data: body.data ?? was.data, version: (was.version ?? 1) + 1, updatedAt: later }
      return route.fulfill({ json: artifacts[single[1]] })
    }
    if (method === 'GET' && /^\/artifacts\/[0-9a-f]{32}\/marks$/.test(path)) return route.fulfill({ json: null })
    if (method === 'POST' && path === '/files') {
      const body = request.postDataBuffer()?.toString('utf8') ?? ''
      const name = body.match(/filename="([^"]+)"/)?.[1] ?? 'file'
      recorded.uploads.push(name)
      return route.fulfill({ status: 201, json: { id: `f${recorded.uploads.length}`.padEnd(32, '0'), name,
        size: 120, mime: 'text/markdown', tokens: 40, projectId: null, sessionId: null, preview: '본문',
        error: null, indexed: true, createdAt: later } })
    }
    if (method === 'GET' && path === `/shared/${shareToken}`) {
      return route.fulfill({ json: { kind: 'session', title: audit.title, sessionKind: 'report',
        messages: reportMessages, startedWith: null,
        artifact: { title: audit.title, artifactKind: 'report', data: report.data, updatedAt: later },
        updatedAt: later } })
    }
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && (/^\/sessions\/[0-9a-f]{32}\/(jobs|runs)$/.test(path) || ['/projects', '/skills',
      '/memory', '/agents', '/tools', '/connectors', '/connectors/catalog', '/jobs', '/designs', '/shares',
      '/pipelines', '/user-pipelines', '/files'].includes(path))) return route.fulfill({ json: [] })
    recorded.unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  return recorded
}

/** The transcript of one of the fixture's sessions, settled. */
export async function openSession(page: Page, id: string) {
  await page.goto(`/s/${id}`)
  await expect(page.getByTestId('transcript')).toBeVisible()
}

export const panel = (page: Page) => page.locator('[data-panel="artifact"]')
export const card = (page: Page) => page.locator('[data-result-card]')
