import { expect, test, type Page } from '@playwright/test'

const sessionId = 'calculator-answer-origin'
const now = '2026-09-12T00:00:00.000Z'
const prompt = '12/0'
const answer = '0으로 나누는 계산은 정의되지 않으므로 값을 구할 수 없습니다.'
const badgeText = '계산 도구 결과 · 0으로 나눌 수 없음'
const footerText = '답변 모델 생성 없음 · 0 in · 0 out · 답변 0 크레딧'
const origin = {
  answerOrigin: 'tool_result', toolName: 'calculate', reasonCode: 'division_by_zero',
  actualModel: null,
}
const privacy = {
  requestedModels: ['fixture/quality'], routedModels: ['fixture/quality'],
  effectiveModels: ['fixture/quality'], actualModels: [], actualModel: 'fixture/quality',
  action: 'mask_external', dataBoundary: 'external', detectorVersion: 'fixture-detector',
  findingCounts: [{ category: 'email', source: 'memory', count: 1 }],
}
const priorRoute = {
  mode: 'auto_quality', decision: 'kept_quality', reasonCode: 'low_complexity',
  requestedModel: 'fixture/quality', selectedModel: 'fixture/quality',
  classifierVersion: 'fixture-router', classifierModel: 'fixture/classifier',
  classifierInputTokens: 44, classifierOutputTokens: 9,
}

/** All API responses are fixtures; a prior classifier is not answer-model generation. */
async function mockAnswer(page: Page, saved: boolean, generated = false, live = true) {
  const requests: Record<string, unknown>[] = []
  const unexpected: string[] = []
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  const messages = [
    { id: 'fixture-question', role: 'user', content: prompt, attachments: [], createdAt: now },
    {
      id: 'fixture-answer', role: 'assistant', content: answer, attachments: [],
      model: generated ? 'fixture/quality' : null,
      routing: generated ? privacy : { ...privacy, ...origin }, createdAt: now,
      usage: { inputTokens: generated ? 8 : 0, outputTokens: generated ? 4 : 0, credits: 0 },
      steps: [{ id: 'h1_0', type: 'tool', label: '계산 확인', status: 'error' }],
    },
  ]
  const row = {
    id: sessionId, title: '계산 오류 확인', kind: 'chat', model: 'fixture/quality',
    routingMode: 'auto_quality', projectId: null, agentId: null, artifactId: null, pinned: false,
    messages: saved ? messages : [], messageCount: saved ? 2 : 0, createdAt: now, updatedAt: now,
  }
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'fixture-user', email: 'fixture@example.test', name: '계산 검증',
        role: 'user', status: 'active', monthlyCredits: 1000, creditsUsed: 0,
        avatarColor: '#168267', allowedModels: [], createdAt: now,
        preferences: { autoMemory: false, showUsage: true, streamResponses: live },
      },
    })
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'fixture/quality',
      models: [{
        id: 'fixture/quality', label: '검증 품질 모델', name: '검증 품질 모델', vendor: 'Fixture',
        provider: 'fixture', kinds: ['chat'], modality: 'chat', dataBoundary: 'external',
        strictLocal: false, privacyOnly: false, creditCost: 0, inputCreditCost: 0,
        supportsTools: true, supportsVision: false, contextWindow: 64000,
      }],
      autoRouting: { enabled: true, available: true, reason: null, economyModelIds: [],
        qualityEnabled: true, qualityAvailable: true, qualityModelIds: ['fixture/quality'] },
    })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && request.method() === 'GET') {
      // A list refresh must not hide a broken event handler by replacing its transcript.
      const { messages: _messages, ...summary } = row
      return json([summary])
    }
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
            { type: 'privacy_route', ...privacy },
            { type: 'model_route', ...priorRoute },
            { type: 'step', id: 'h1_0', label: '계산 확인', status: 'error' },
            ...(!generated ? [{ type: 'tool_result_answer', ...origin }] : []),
            { type: 'delta', text: answer },
            { type: 'usage', ...messages[1].usage },
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

async function assertToolAnswer(page: Page) {
  const badge = page.getByText(badgeText, { exact: true })
  await expect(badge).toBeVisible()
  await expect(badge).toBeInViewport()
  const footer = page.getByText(footerText, { exact: true })
  await expect(footer).toBeVisible()
  await expect(footer).toBeInViewport()
  if (page.viewportSize()!.width < 640) {
    expect(await footer.evaluate((node) =>
      node.getBoundingClientRect().top >= node.previousElementSibling!.getBoundingClientRect().bottom,
    )).toBe(true)
  }
  await expect(page.getByText(/검증 품질 모델 · \d+ in/)).toHaveCount(0)
  await expect(badge.locator('..').getByText(/Auto ·/)).toHaveCount(0)
  await expect(page.getByText(/서비스 정책 안내|최신 정보 검증 불가|모델 실행 없음|확인 중…/)).toHaveCount(0)
  const details = page.getByRole('button', { name: /처리 내역 \d+건/ })
  if (await details.isVisible()) await details.click()
  await expect(page.getByText('개인정보를 가려 전송함', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  const fitsParent = await badge.evaluate((node) => {
    const box = node.getBoundingClientRect()
    const parent = node.parentElement!.getBoundingClientRect()
    return box.left >= parent.left && box.right <= parent.right + 1
  })
  expect(fitsParent).toBe(true)
}

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  for (const mode of ['saved', 'streamed'] as const) {
    test(`${viewport.name} ${mode}: 계산기 답변은 모델 생성과 Auto 선택을 구분하며 재조회 후에도 유지한다`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      const state = await mockAnswer(page, mode === 'saved')
      await page.goto(`/s/${sessionId}`)
      if (mode === 'streamed') {
        const input = page.getByLabel('프롬프트 입력')
        await input.fill(prompt)
        await input.press('Enter')
      }
      await expect(page.getByText(answer, { exact: true })).toBeVisible()
      await assertToolAnswer(page)
      if (process.env.CALCULATOR_ANSWER_SCREENSHOT_DIR) {
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({
          path: `${process.env.CALCULATOR_ANSWER_SCREENSHOT_DIR}/calculator-answer-${mode}-${viewport.name}.png`,
          animations: 'disabled',
        })
      }
      await page.reload()
      await assertToolAnswer(page)
      await page.getByRole('button', { name: '언어 전환 · EN', exact: true }).click()
      await expect(page.getByText('Calculator result · Cannot divide by zero', { exact: true })).toBeVisible()
      await expect(page.getByText('No answer-model generation · 0 in · 0 out · Answer: 0 credits', { exact: true })).toBeVisible()
      expect(state.requests.map((request) => request.content)).toEqual(mode === 'saved' ? [] : [prompt])
      expect(state.unexpected).toEqual([])
    })
  }
}

test('동일한 오류 문구도 모델이 작성했다면 도구 출처를 추측하지 않는다', async ({ page }) => {
  const state = await mockAnswer(page, true, true)
  await page.goto(`/s/${sessionId}`)
  await expect(page.getByText(answer, { exact: true })).toBeVisible()
  await expect(page.getByText(/검증 품질 모델 · 8 in · 4 out/)).toBeVisible()
  await expect(page.getByText(badgeText, { exact: true })).toHaveCount(0)
  await expect(page.getByText(/답변 모델 생성 없음/)).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})

test('텍스트 스트리밍을 꺼도 출처 이벤트를 처리하고 답변 비용을 구분한다', async ({ page }) => {
  const state = await mockAnswer(page, false, false, false)
  await page.goto(`/s/${sessionId}`)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill(prompt)
  await input.press('Enter')
  await expect(page.getByText(answer, { exact: true })).toBeVisible()
  await assertToolAnswer(page)
  expect(state.unexpected).toEqual([])
})
