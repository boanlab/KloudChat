import { expect, test, type Page } from '@playwright/test'

const sessionId = 'calculation-refusal'
const now = '2026-09-12T00:00:00.000Z'
const prompt = '판매가 24,000원에서 15% 할인한 가격을 계산해 줘.'
const notice = '현재 모델이나 에이전트에서 허용된 계산 도구를 사용할 수 없습니다. 도구를 지원하는 모델로 바꾸거나 계산기를 허용한 에이전트로 다시 시도하세요.'

/** All API requests stay in this fixture; no account, backend or model is used. */
async function mockCalculationRefusal(page: Page) {
  const requests: Record<string, unknown>[] = []
  const unexpected: string[] = []
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  const row = {
    id: sessionId, title: '할인 금액 확인', kind: 'chat', model: 'fixture/calculation',
    routingMode: 'manual', projectId: null, agentId: null, artifactId: null,
    pinned: false, messages: [], messageCount: 0, createdAt: now, updatedAt: now,
  }
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'calculation-user', email: 'calculation@example.com', name: '계산 확인',
        role: 'user', status: 'active', monthlyCredits: 1000, creditsUsed: 0,
        avatarColor: '#168267', allowedModels: [], createdAt: now,
        preferences: { autoMemory: false, showUsage: false, streamResponses: true },
      },
    })
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'fixture/calculation',
      models: [{
        id: 'fixture/calculation', label: '검증 모델', name: '검증 모델', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat'], modality: 'chat', dataBoundary: 'self_hosted',
        strictLocal: false, privacyOnly: false, creditCost: 0, inputCreditCost: 0,
        supportsTools: true, supportsVision: false, contextWindow: 64000,
      }],
    })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && request.method() === 'GET') return json([row])
    if (path === `/sessions/${sessionId}` && request.method() === 'GET') return json(row)
    if (path === `/sessions/${sessionId}/messages`) {
      if (request.method() === 'GET') return json([])
      if (request.method() === 'POST') {
        requests.push(request.postDataJSON())
        return route.fulfill({ status: 409, json: { detail: 'calculation_tool_unavailable' } })
      }
    }
    if (path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    if (request.method() === 'GET' && [
      '/projects', '/artifacts', '/skills', '/memory', '/agents', '/tools', '/templates',
      '/connectors', '/connectors/catalog', '/jobs', '/designs', '/design-templates',
      '/prompt-templates', '/shares', `/sessions/${sessionId}/jobs`,
    ].includes(path)) return json([])
    unexpected.push(`${request.method()} ${path}`)
    return route.fulfill({ status: 501, json: { detail: 'Unmocked fixture request' } })
  })
  return { requests, unexpected }
}

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  test(`${viewport.name}: 계산 도구 거부는 복구 방법을 안내하고 재시도할 질문을 보존한다`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    const state = await mockCalculationRefusal(page)
    await page.goto(`/s/${sessionId}`)
    const input = page.getByLabel('프롬프트 입력')
    await expect(input).toBeVisible()
    await input.fill(prompt)
    await input.press('Enter')

    const message = page.getByText(notice, { exact: true })
    await expect(message).toBeVisible()
    await expect(message).toBeInViewport()
    await expect(input).toHaveValue(prompt)
    await expect(input).toBeEnabled()
    await expect(page.getByText(/calculation_tool_unavailable/)).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
    const box = await message.boundingBox()
    expect(box).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width)
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height)

    if (process.env.CALCULATION_REFUSAL_SCREENSHOT_DIR) {
      await page.evaluate(() => document.fonts.ready)
      await page.screenshot({
        path: `${process.env.CALCULATION_REFUSAL_SCREENSHOT_DIR}/calculation-refusal-${viewport.name}.png`,
        animations: 'disabled',
      })
    }
    await input.press('Enter')
    await expect.poll(() => state.requests.length).toBe(2)
    await expect(message).toBeVisible()
    await expect(input).toHaveValue(prompt)
    expect(state.requests.map((request) => request.content)).toEqual([prompt, prompt])
    expect(state.unexpected).toEqual([])
  })
}
