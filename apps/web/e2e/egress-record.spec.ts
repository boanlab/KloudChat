import { expect, test, type Page } from '@playwright/test'

/**
 * An answer whose turn went to an outside model with personal data masked shows what left,
 * as it left (`routing.egress`): 「외부로 보낸 내용 보기」 opens the masked request and
 * material with each placeholder marked. A strict-local answer shows nothing of the kind.
 * Every API response is a fixture.
 */

const sessionId = 'egress-record'
const now = '2026-11-21T00:00:00.000Z'
const external = 'external/flash'
const strict = 'strict-local/qwen3.8-flash'
const shots = process.env.EGRESS_SCREENSHOT_DIR

const maskedRouting = {
  requestedModels: [external], routedModels: [external], effectiveModels: [external],
  actualModels: [], action: 'mask_external', dataBoundary: 'external',
  findingCounts: [
    { category: 'email', source: 'current_input', count: 1 },
    { category: 'phone', source: 'current_input', count: 1 },
    { category: 'email', source: 'attachments', count: 1 },
  ],
  egress: {
    masked: [
      { category: 'email', label: '[이메일]', count: 2 },
      { category: 'phone', label: '[전화번호]', count: 1 },
    ],
    parts: [
      { source: 'prompt', text: '[이메일], [전화번호] 로 회원에게 보낼 안내문을 써 줘' },
      { source: 'attachments', text: '이름,이메일\n한유진,[이메일]\n김하늘,가입 2024-03-02' },
    ],
  },
}
const strictRouting = {
  requestedModels: [strict], routedModels: [strict], effectiveModels: [strict],
  actualModels: [], action: 'strict_local', dataBoundary: 'self_hosted',
  findingCounts: [{ category: 'email', source: 'current_input', count: 1 }],
}

const messages = [
  { id: 'q1', role: 'user', content: '[이메일], [전화번호] 로 회원에게 보낼 안내문을 써 줘',
    attachments: [], createdAt: now, routing: { ...maskedRouting, egress: undefined } },
  { id: 'a1', role: 'assistant', content: '회원님께, 가입해 주셔서 감사합니다.', attachments: [],
    model: external, createdAt: now, routing: maskedRouting,
    usage: { inputTokens: 300, outputTokens: 40, credits: 2 } },
  { id: 'q2', role: 'user', content: 'sky.kim@example.com 형식이 맞아?', attachments: [],
    createdAt: now, routing: strictRouting },
  { id: 'a2', role: 'assistant', content: 'sky.kim@example.com 은 올바른 형식입니다.', attachments: [],
    model: strict, createdAt: now, routing: strictRouting,
    usage: { inputTokens: 120, outputTokens: 20, credits: 0 } },
]

async function mock(page: Page) {
  const unexpected: string[] = []
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  const row = {
    id: sessionId, title: '회원 안내문', kind: 'chat', model: external, routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pinned: false, messages,
    messageCount: messages.length, createdAt: now, updatedAt: now,
  }
  const catalogueRow = (id: string, label: string, boundary: string, strictLocal: boolean) => ({
    id, label, name: label, vendor: 'Mock', provider: 'mock', kinds: ['chat'], modality: 'chat',
    dataBoundary: boundary, strictLocal, privacyOnly: false, creditCost: strictLocal ? 0 : 1,
    inputCreditCost: 0, supportsTools: true, supportsVision: false, contextWindow: 64000,
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'fixture-user', email: 'fixture@example.test', name: '담당자', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
        allowedModels: [], createdAt: now,
        preferences: { autoMemory: false, showUsage: true, streamResponses: true },
      },
    })
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: true, piiMasking: true, strictLocalWebSearch: 'ask' },
      passwordResetEnabled: false, dictationEnabled: false,
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: external,
      models: [catalogueRow(external, 'Mock · Flash', 'external', false),
        catalogueRow(strict, 'qwen3.8-flash (strict-local)', 'self_hosted', true)],
    })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && request.method() === 'GET') {
      const { messages: _messages, ...summary } = row
      return json([summary])
    }
    if (path === `/sessions/${sessionId}` && request.method() === 'GET') return json(row)
    if (path === `/sessions/${sessionId}/messages` && request.method() === 'GET') return json(messages)
    if (request.method() === 'GET') return json([])
    return route.abort('blockedbyclient')
  })
  return { unexpected }
}

async function shot(page: Page, name: string) {
  if (!shots) return
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: `${shots}/${name}.png`, animations: 'disabled', fullPage: false })
}

test('외부로 가려 보낸 답은 보낸 내용과 가린 개수를 보여 주고, strict-local 답은 아무것도 붙지 않는다 @phone', async ({ page }) => {
  const state = await mock(page)
  await page.goto(`/s/${sessionId}`)
  await expect(page.getByText('회원님께, 가입해 주셔서 감사합니다.')).toBeVisible()
  const toggles = page.getByTestId('egress-toggle')
  // only the masked external answer carries it
  await expect(toggles).toHaveCount(1)
  // the strict-local question was kept as typed: no 「가려진 채 저장」 notice on it
  await expect(page.getByText('기록에는 가려진 채 저장됩니다. 이 대화를 다시 열면 여기에도 자리표시자만 남습니다.'))
    .toHaveCount(1)
  const toggle = toggles.first()
  await expect(toggle).toContainText('외부로 보낸 내용 보기')
  await expect(toggle).toContainText('이메일 2 · 전화번호 1 가림')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await shot(page, `closed-${page.viewportSize()!.width}`)
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  const panel = page.getByTestId('egress-panel')
  await expect(panel).toBeVisible()
  await expect(panel.getByTestId('egress-part-prompt')).toContainText('보낸 요청')
  await expect(panel.getByTestId('egress-part-attachments')).toContainText('한유진,[이메일]')
  await expect(panel.getByTestId('egress-placeholder')).toHaveCount(3)
  await expect(panel.getByTestId('egress-placeholder').first()).toHaveText('[이메일]')
  await expect(panel).not.toContainText('@')
  // the panel stays on the screen at every width
  const box = (await panel.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth))
    .toBeLessThanOrEqual(0)
  await panel.scrollIntoViewIfNeeded()
  await shot(page, `open-${page.viewportSize()!.width}`)
  await toggle.click()
  await expect(panel).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})
