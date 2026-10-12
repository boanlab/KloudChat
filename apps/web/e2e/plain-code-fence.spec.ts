import { expect, test } from '@playwright/test'
import { shellRead } from './shell-mocks'

/** A ``` fence without a language renders as a code block, one line per line (verify2 N5:
 *  the 「회의록 양식」 answer read as one run-on line). All API responses are fixtures. */

const sessionId = 'plain-code-fence'
const now = '2026-10-12T00:00:00.000Z'
const answer = [
  '아래 양식을 그대로 쓰시면 됩니다.',
  '',
  '```',
  '회의명:',
  '일시:',
  '참석자:',
  '```',
  '',
  '빈칸은 `[담당자]`처럼 남겨 두세요.',
].join('\n')

test('언어 표시 없는 코드 블록도 줄을 지킨 블록으로 보인다', async ({ page }) => {
  const unexpected: string[] = []
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  const messages = [
    { id: 'q', role: 'user', content: '학과 회의록 양식 줘', attachments: [], createdAt: now },
    {
      id: 'a', role: 'assistant', content: answer, attachments: [], model: 'fixture/quality',
      createdAt: now, usage: { inputTokens: 8, outputTokens: 4, credits: 0 },
    },
  ]
  const row = {
    id: sessionId, title: '회의록 양식', kind: 'chat', model: 'fixture/quality',
    routingMode: 'manual', projectId: null, agentId: null, artifactId: null, pinned: false,
    messages, messageCount: 2, createdAt: now, updatedAt: now,
  }
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'fixture-user', email: 'fixture@example.test', name: '양식 검증', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
        allowedModels: [], createdAt: now,
        preferences: { autoMemory: false, showUsage: true, streamResponses: true },
      },
    })
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'fixture/quality',
      models: [{
        id: 'fixture/quality', label: '검증 모델', name: '검증 모델', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat'], modality: 'chat', dataBoundary: 'external',
        strictLocal: false, privacyOnly: false, creditCost: 0, inputCreditCost: 0,
        supportsTools: true, supportsVision: false, contextWindow: 64000,
      }],
      autoRouting: { enabled: false, available: false, reason: null, economyModelIds: [],
        qualityEnabled: false, qualityAvailable: false, qualityModelIds: [] },
    })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && request.method() === 'GET') {
      const { messages: _messages, ...summary } = row
      return json([summary])
    }
    if (path === `/sessions/${sessionId}` && request.method() === 'GET') return json(row)
    if (path === `/sessions/${sessionId}/messages` && request.method() === 'GET') return json(messages)
    if (path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    const shell = shellRead(request.method(), path)
    if (shell) return route.fulfill(shell)
    if (request.method() === 'GET' && [
      '/projects', '/artifacts', '/skills', '/memory', '/agents', '/tools',
      '/connectors', '/connectors/catalog', '/jobs', '/designs', '/design-templates',
      '/shares', `/sessions/${sessionId}/jobs`,
    ].includes(path)) return json([])
    unexpected.push(`${request.method()} ${path}`)
    return route.fulfill({ status: 501, json: { detail: 'Unmocked fixture request' } })
  })

  await page.goto(`/s/${sessionId}`)
  const block = page.locator('pre').filter({ hasText: '회의명:' })
  await expect(block).toBeVisible()
  // One line per field: three line boxes, not one run-on line.
  expect(await block.innerText()).toBe('회의명:\n일시:\n참석자:')
  const lineHeight = await block.evaluate((node) => parseFloat(getComputedStyle(node).lineHeight))
  expect((await block.boundingBox())!.height).toBeGreaterThan(lineHeight * 2.5)
  await expect(block.locator('xpath=..').getByRole('button', { name: '복사' })).toBeVisible()
  // Inline code stays inline.
  await expect(page.locator('pre').filter({ hasText: '[담당자]' })).toHaveCount(0)
  await expect(page.locator('code', { hasText: '[담당자]' })).toBeVisible()
  expect(unexpected).toEqual([])
})
