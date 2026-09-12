import { expect, test, type Page } from '@playwright/test'
import type { CostRouting } from '../src/types'

const sessionId = 'auto-calculation-reason'
const now = '2026-09-12T00:00:00.000Z'
const prompt = '17 * 23은 얼마야? 계산식과 답만 짧게 써줘.'
const answer = '17 × 23 = 391'
const reason = 'Auto · 계산 도구 사용을 위해 품질 모델 유지'
const englishReason = 'Auto · Keeping the quality model for calculation tools'
const costRouting: CostRouting = {
  mode: 'auto', decision: 'bypassed', reasonCode: 'calculation_required',
  requestedModel: 'fixture/base', selectedModel: 'fixture/base', executedModel: 'fixture/base',
  classifierVersion: 'adaptive-router-v1',
}
const routing = {
  requestedModels: ['fixture/base'], routedModels: ['fixture/base'],
  effectiveModels: ['fixture/base'], actualModels: ['fixture/base'],
  actualModel: 'fixture/base', action: 'none', dataBoundary: 'external',
}
const messages = [
  { id: 'fixture-question', role: 'user', content: prompt, attachments: [], createdAt: now },
  {
    id: 'fixture-answer', role: 'assistant', content: answer, attachments: [],
    model: 'fixture/base', routing: { ...routing, costRouting }, createdAt: now,
  },
]

/** Every API request is fulfilled locally, including unknown paths. */
async function mockCalculationRoute(page: Page, saved: boolean) {
  const requests: Record<string, unknown>[] = []
  const unexpected: string[] = []
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  const row = {
    id: sessionId, title: 'Auto 계산 확인', kind: 'chat', model: 'fixture/base',
    routingMode: 'auto', projectId: null, agentId: null, artifactId: null, pinned: false,
    messages: saved ? messages : [], messageCount: saved ? 2 : 0, createdAt: now, updatedAt: now,
  }
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'fixture-user', email: 'fixture@example.com', name: 'Auto 검증',
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
      litellmAvailable: true, defaultChatModel: 'fixture/base',
      models: [{
        id: 'fixture/base', label: '검증 모델', name: '검증 모델', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat'], modality: 'chat', dataBoundary: 'external',
        strictLocal: false, privacyOnly: false, creditCost: 0, inputCreditCost: 0,
        supportsTools: true, supportsVision: false, contextWindow: 64000,
      }],
      autoRouting: { enabled: true, available: true, reason: null, economyModelIds: [] },
    })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && request.method() === 'GET') return json([row])
    if (path === `/sessions/${sessionId}` && request.method() === 'GET') return json(row)
    if (path === `/sessions/${sessionId}/messages`) {
      if (request.method() === 'GET') return json(row.messages)
      if (request.method() === 'POST') {
        requests.push(request.postDataJSON())
        row.messages = messages
        row.messageCount = messages.length
        return route.fulfill({
          status: 200,
          headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
          body: [
            { type: 'model_route', ...costRouting },
            { type: 'model_route', routedModel: 'fixture/base', actualModel: 'fixture/base' },
            { type: 'privacy_route', ...routing },
            { type: 'delta', text: answer },
            { type: 'usage', inputTokens: 3, outputTokens: 1, credits: 0 },
            { type: 'done' },
          ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
        })
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
  for (const mode of ['saved', 'streamed'] as const) {
    test(`${viewport.name} ${mode}: Auto 계산 도구 보존 이유와 실행 모델을 표시한다`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      const state = await mockCalculationRoute(page, mode === 'saved')
      await page.goto(`/s/${sessionId}`)
      if (mode === 'streamed') {
        const input = page.getByLabel('프롬프트 입력')
        await input.fill(prompt)
        await input.press('Enter')
      }
      await expect(page.getByText(answer, { exact: true })).toBeVisible()
      const details = page.getByRole('button', { name: /처리 내역 \d+건/ })
      if (await details.isVisible()) await details.click()
      const badge = page.getByText(`${reason} · 요청 모델: 검증 모델 · 선택 모델: 검증 모델 · 실행 모델: 검증 모델`, { exact: true })
      await expect(badge).toBeVisible()
      await expect(badge).toBeInViewport()
      await expect(page.getByText(/Auto · 난이도 판정을 생략하고 품질 모델 유지/)).toHaveCount(0)
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
      const box = await badge.boundingBox()
      expect(box).not.toBeNull()
      expect(box!.x).toBeGreaterThanOrEqual(0)
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width)
      expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height)
      if (process.env.AUTO_CALCULATION_REASON_SCREENSHOT_DIR) {
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({
          path: `${process.env.AUTO_CALCULATION_REASON_SCREENSHOT_DIR}/auto-calculation-reason-${mode}-${viewport.name}.png`,
          animations: 'disabled',
        })
      }
      await page.getByRole('button', { name: '언어 전환 · EN', exact: true }).click()
      await expect(page.getByText(new RegExp(englishReason))).toBeVisible()
      expect(state.requests.map((request) => request.content)).toEqual(mode === 'saved' ? [] : [prompt])
      expect(state.unexpected).toEqual([])
    })
  }
}
