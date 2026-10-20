import { expect, test, type Page, type TestInfo } from '@playwright/test'
import style from './fixtures/report-table-style.json' with { type: 'json' }
import { shellRead } from './shell-mocks'

/** A `data:` picture the writer typed out and cut short is never shown as base64 text in the web view. */

test.use({ serviceWorkers: 'block' })

const sessionId = '55555555555555555555555555555555'
const artifactId = '66666666666666666666666666666666'
const at = '2026-10-19T00:00:00.000Z'
// Cut off by the writer: no closing parenthesis.
const invented = '![그림 1. 흐릿한 시장 상황](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAABIAAAANgCAIAAADbKYQBAAEAAElEQVR4AQAKh/V4AXWFk'
// 1×1 PNG.
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

async function fixture(page: Page, testInfo: TestInfo) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const writes: { data: Record<string, unknown> }[] = []
  let artifact = {
    id: artifactId, title: '시험 문서', kind: 'report', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { kind: 'report', title: '시험 문서', sources: [],
      sections: [
        { id: 's1', heading: '시장 상황', level: 1, status: 'done', content: `반응이 흐릿하다.\n\n${invented}\n\n다음 문단.` },
        { id: 's2', heading: '그림', level: 1, status: 'done', content: `실제 그림.\n\n![그림 2. 실제 그림](data:image/png;base64,${PNG})` },
      ] },
  }
  const session = { id: sessionId, title: artifact.title, kind: 'report', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId, pinned: false, messages: [], messageCount: 0,
    made: null, createdAt: at, updatedAt: at }
  await page.context().routeWebSocket('**/*', (socket) => { unexpected.push('WebSocket'); socket.close() })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'GET' && path === '/design-templates/_document/style') return route.fulfill({ json: style })
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: 'Report fixture', email: 'fixture@example.test', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
        createdAt: at, preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'fixture/base', label: 'Fixture',
      name: 'Fixture', vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report'], modality: 'chat',
      dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/base', litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [session] })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: [artifact] })
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return route.fulfill({ json: artifact })
    if (method === 'PATCH' && path === `/artifacts/${artifactId}`) {
      const patch = request.postDataJSON()
      writes.push(patch)
      artifact = { ...artifact, title: patch.title ?? artifact.title, data: patch.data, version: artifact.version + 1 }
      return route.fulfill({ json: artifact })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: { report: 1 }, total: 1 } })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && [`/sessions/${sessionId}/messages`, `/sessions/${sessionId}/jobs`, '/projects',
      '/skills', '/memory', '/agents', '/tools', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/design-templates', '/shares'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.abort('blockedbyclient')
  })
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  return { unexpected, writes }
}

test('an invented picture shows its caption in italics, a real one its image', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  const web = page.locator('article.doc-web')
  await expect(web.getByText('다음 문단.')).toBeVisible()
  await expect(web).not.toContainText('base64')
  await expect(web.locator('em', { hasText: '그림 1. 흐릿한 시장 상황' })).toBeVisible()
  await expect(web.locator('img[alt="그림 2. 실제 그림"]')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('broken-picture-web.png') })
  expect(state.unexpected).toEqual([])
})
