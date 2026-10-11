import { expect, test } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * A report being written wears the look it will be saved with. The server sends `design`
 * before the first section; the stream here stops before the saved artifact, so only the
 * draft is on screen.
 */

test.use({ serviceWorkers: 'block' })

const at = '2026-10-09T07:00:00.000Z'
const sessionId = 'a4444444444444444444444444444444'
const request = '사내 클라우드 보안 점검 결과를 정리한 1쪽 보고서. 점검 항목 5개, 발견 사항, 개선 권고로 구성'
const headings = ['점검 개요 및 범위', '점검 항목 5개 기준', '발견 사항', '개선 권고']

function sse(events: unknown[]) {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
}

test('the draft report wears the look it is saved with', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the ribbon is the same on every width')
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const session = {
    id: sessionId, kind: 'report', title: '사내 클라우드 보안 점검', projectId: null, agentId: null,
    model: 'fixture/base', routingMode: 'manual', artifactId: null, skillIds: [], renderTemplateId: null,
    pinned: false, createdAt: at, updatedAt: at, preview: null, messageCount: 2, made: null,
    pending: { stage: 'outline', request, attachments: [], answers: {},
      plan: { title: '사내 클라우드 보안 점검 결과', visualStyle: 'minimal', sections: headings } },
    messages: [
      { id: 'm1', role: 'user', content: request, createdAt: at },
      { id: 'm2', role: 'assistant', content: '이렇게 구성하려고 합니다. 확인해 주세요.', createdAt: at },
    ],
  }

  await page.context().routeWebSocket(/^(?!wss?:\/\/[^/]+\/\?token=).*$/, (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = req.method()
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: 'Look fixture', email: 'fixture@example.test', role: 'user', status: 'active',
        monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
        preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report', 'slides'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'fixture/base', label: 'Fixture',
      name: 'Fixture', vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report', 'slides'], modality: 'chat',
      dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/base', litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [{ ...session, messages: null }] })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'POST' && path === `/sessions/${sessionId}/messages`) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse([
        { type: 'title', title: '사내 클라우드 보안 점검 결과' },
        { type: 'design', design: { accent: '#334155', ink: '#1a1a1a', muted: '#666666', font: 'serif',
          visualStyle: 'minimal', footer: '', logo: '' } },
        { type: 'sources', sources: [] },
        ...headings.map((heading, i) => ({ type: 'section', sectionId: `s${i}`, heading, content: '', done: false })),
        { type: 'section', sectionId: 's0', heading: headings[0], content: '점검 범위를 정리했다.', done: true },
      ]) })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    if (method === 'GET' && (path.endsWith('/messages') || path.endsWith('/jobs'))) return route.fulfill({ json: [] })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && ['/artifacts', '/projects', '/skills', '/memory', '/tools', '/connectors', '/connectors/catalog',
      '/jobs', '/designs', '/shares', '/design-templates', '/skills/store', '/agents',
      '/user-pipelines', '/user-pipelines/store'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })

  await page.goto(`/s/${sessionId}`)
  await page.getByRole('button', { name: '이대로 생성' }).click()
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel.getByText('점검 범위를 정리했다.')).toBeVisible({ timeout: 15_000 })
  const look = panel.getByRole('button', { name: '문서 디자인 고르기' })
  await expect(look).toContainText('미니멀')
  await expect(look).not.toContainText('편집형')
  const accent = panel.getByRole('button', { name: '강조색 고르기' })
  await expect(accent).toContainText('먹')
  await expect(accent).not.toContainText('보라')
  expect(unexpected).toEqual([])
})
