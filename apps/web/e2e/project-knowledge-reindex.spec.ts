import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * Project 지식: a document the meaning search does not cover yet says 색인 안 됨, and 색인하기
 * sends it again (`POST /projects/{id}/knowledge/reindex`), as an agent's knowledge does.
 */

test.use({ serviceWorkers: 'block' })

const projectId = '44444444444444444444444444444444'
const at = '2026-11-10T00:00:00.000Z'
const user = {
  id: 'fixture-user', name: '색인 검증', email: 'fixture@example.test', role: 'user', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}

const file = (id: string, name: string, indexed: boolean) => ({
  id, name, size: 2048, mime: 'text/markdown', tokens: 320, projectId, sessionId: null,
  preview: '실험 조건', error: null, indexed, ocrState: null, createdAt: at,
})

async function fixture(page: Page, info: TestInfo, answer: 'ok' | 'unavailable') {
  const origin = new URL(String(info.project.use.baseURL)).origin
  const unexpected: string[] = []
  const reindexed: string[] = []
  let indexed = false
  const project = () => ({
    id: projectId, name: '실험 노트', description: '', emoji: '📁', instructions: '', skillIds: [],
    designSystemId: null, renderTemplates: {}, sessionIds: [], createdAt: at, updatedAt: at,
    files: [file('f1', '조건.md', indexed), file('f2', '결과.md', true)],
  })
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (path === '/auth/refresh') {
      return json({ accessToken: 'fixture-only', expiresIn: 3600, user })
    }
    if (method === 'GET' && path === '/auth/config') {
      return json({ brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
        privacy: { externalDataGuard: false }, passwordResetEnabled: false,
        dictationEnabled: false })
    }
    if (method === 'GET' && path === '/models') return json({ models: [], defaultChatModel: '',
      litellmAvailable: false, autoRouting: { enabled: false, available: false } })
    if (method === 'GET' && path === '/credits') {
      return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    }
    if (method === 'GET' && path === '/projects') return json([project()])
    if (method === 'GET' && path === `/projects/${projectId}`) return json(project())
    if (method === 'POST' && path === `/projects/${projectId}/knowledge/reindex`) {
      reindexed.push(url.search)
      if (answer === 'unavailable') return json({ detail: 'index_unavailable' }, 503)
      indexed = true
      return json({ total: 2, attempted: 1, indexed: 1 })
    }
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/sessions', '/agents', '/skills', '/connectors', '/artifacts',
      '/designs', '/design-templates', '/memory', '/connectors/catalog', '/tools', '/jobs']
      .includes(path)) {
      return json([])
    }
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'Unmocked fixture request' }, 501)
  })
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: /^지식/ }).click()
  return { unexpected, reindexed }
}

const row = (page: Page, id: string) => page.locator(`[data-knowledge="${id}"]`)

test('색인하기 sends the unindexed documents and the marks clear', async ({ page }, info) => {
  const state = await fixture(page, info, 'ok')
  await expect(row(page, 'f1')).toContainText('색인 안 됨')
  await expect(row(page, 'f2')).not.toContainText('색인 안 됨')
  await page.getByRole('button', { name: '색인하기' }).click()
  await expect(page.getByRole('status').filter({ hasText: '1/1개 문서를 색인했습니다.' })).toBeVisible()
  await expect(row(page, 'f1')).not.toContainText('색인 안 됨')
  // Nothing left to send: the button goes.
  await expect(page.getByRole('button', { name: '색인하기' })).toHaveCount(0)
  // Only what is missing, not everything again.
  expect(state.reindexed).toEqual([''])
  expect(state.unexpected).toEqual([])
})

test('an unreachable index service says so, and the marks stay', async ({ page }, info) => {
  const state = await fixture(page, info, 'unavailable')
  await page.getByRole('button', { name: '색인하기' }).click()
  await expect(page.getByRole('alert')).toContainText('검색 색인 서비스에 연결할 수 없습니다')
  await expect(row(page, 'f1')).toContainText('색인 안 됨')
  await expect(page.getByRole('button', { name: '색인하기' })).toBeEnabled()
  expect(state.unexpected).toEqual([])
})
