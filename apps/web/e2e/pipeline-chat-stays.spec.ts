import { expect, test, type Page } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * A conversation that runs a user pipeline (「이 워크플로로 시작」 on a chat flow) answers its
 * first message there: a sentence that reads like a document order is the pipeline's input,
 * not a cue to move to a new report conversation (which would leave the pipeline behind).
 */

const id = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
const otherId = 'cccccccccccccccccccccccccccccccc'
const reportId = 'dddddddddddddddddddddddddddddddd'
const otherChatId = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
const flowChat = 'ffffffffffffffffffffffffffffffff'
const at = '2026-09-08T00:00:00.000Z'

async function fixture(page: Page, extra: Record<string, unknown>) {
  const sessions: Record<string, unknown>[] = [
    { id: otherId, title: '다른 대화', kind: 'chat' },
    { id: reportId, title: '다른 보고서', kind: 'report' },
    { id: otherChatId, title: '별도 대화', kind: 'chat' },
    { id: flowChat, title: '워크플로 대화', kind: 'chat', ...extra },
  ].map((row) => ({ model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pinned: false,
    messages: [], messageCount: 0, made: null, createdAt: at, updatedAt: at, ...row }))
  const writes: { path: string; body: Record<string, unknown> }[] = []
  const models = ['base', 'economy', 'quality'].map((name) => ({
    id: `fixture/${name}`, label: `Fixture ${name}`, name: `Fixture ${name}`,
    vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report'], modality: 'chat',
    dataBoundary: 'external', strictLocal: false, privacyOnly: false,
    creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000,
  }))
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace('/api', '')
    const method = request.method()
    if (path === '/auth/refresh') {
      return route.fulfill({ json: { accessToken: 'fixture-token', expiresIn: 3600,
        user: { id: 'fixture-user', name: '복원 검증', email: 'fixture@example.test',
          role: 'user', status: 'active', monthlyCredits: 1000, creditsUsed: 0,
          avatarColor: '#168267', allowedModels: [], createdAt: at,
          preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    }
    if (path === '/auth/config') {
      return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
        enabledKinds: ['chat', 'report'], privacy: { externalDataGuard: false },
        passwordResetEnabled: false, dictationEnabled: false } })
    }
    if (path === '/models') {
      return route.fulfill({ json: { models, defaultChatModel: 'fixture/base',
        litellmAvailable: true, autoRouting: { enabled: true, available: true,
          economyModelIds: ['fixture/economy'], qualityEnabled: true,
          qualityAvailable: true, qualityModelIds: ['fixture/quality'] } } })
    }
    if (path === '/credits') {
      return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    }
    if (path === '/sessions' && method === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, body })
      const row = { id, title: '복원 검증', kind: 'chat', model: 'fixture/base',
        routingMode: 'manual', projectId: null, agentId: null, artifactId: null,
        pinned: false, messages: [], messageCount: 0, createdAt: at, updatedAt: at, ...body }
      sessions.push(row)
      return route.fulfill({ status: 201, json: row })
    }
    if (path === '/sessions' && method === 'GET') return route.fulfill({ json: sessions })
    if (/^\/sessions\/[^/]+$/.test(path) && method === 'GET') {
      const session = sessions.find((row) => row.id === path.split('/')[2])
      return route.fulfill({ status: session ? 200 : 404, json: session ?? {} })
    }
    if (/^\/sessions\/[^/]+\/messages$/.test(path) && method === 'GET') {
      return route.fulfill({ json: [] })
    }
    if (path === '/files' && method === 'POST') {
      return route.fulfill({ status: 201, json: { id: 'fixture-file', name: 'draft.txt',
        mime: 'text/plain', size: 5, tokens: 2, preview: 'draft', error: null,
        projectId: null, sessionId: null, createdAt: at } })
    }
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/projects', '/artifacts', '/skills', '/memory', '/agents',
      '/tools', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/design-templates'].includes(path)) {
      return route.fulfill({ json: [] })
    }
    if (method !== 'GET') writes.push({ path, body: request.postDataJSON() ?? {} })
    return route.abort('blockedbyclient')
  })
  return writes
}


for (const [label, extra, stays] of [
  ['a conversation bound to a pipeline keeps the turn', { pipelineId: 'p1' }, true],
  ['a plain conversation still moves a document order to the report', {}, false],
] as const) {
  test(label, async ({ page }) => {
    const writes = await fixture(page, extra)
    await page.goto(`/s/${flowChat}`)
    await page.getByLabel('프롬프트 입력').fill('자기소개서 지원동기 문항 보고서로 써 줘.')
    await page.getByLabel('프롬프트 입력').press('Enter')
    await expect.poll(() => writes.length).toBeGreaterThan(0)
    const paths = writes.map((w) => w.path)
    if (stays) {
      expect(paths[0]).toBe(`/sessions/${flowChat}/messages`)
      expect(paths).not.toContain('/sessions')
    } else {
      expect(paths[0]).not.toBe(`/sessions/${flowChat}/messages`)
    }
  })
}
