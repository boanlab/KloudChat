import { expect, test, type Page } from '@playwright/test'

const sessionId = 'masking-source-fixture'
const now = '2026-09-13T00:00:00.000Z'
const prompt = '공개 자료에서 서비스 이용 시간을 찾아줘'
const answer = '조회한 자료를 정리했습니다. 적용 시점은 출처에서 확인해야 합니다.'
const finding = (source: string) => ({ category: 'api_key', source, count: 1 })
const baseRouting = {
  requestedModels: ['fixture/model'], routedModels: ['fixture/model'],
  effectiveModels: ['fixture/model'], actualModels: ['fixture/model'],
  actualModel: 'fixture/model', action: 'none', dataBoundary: 'hybrid',
  findingCounts: [] as ReturnType<typeof finding>[],
}

const scenarios = {
  tool: {
    initial: baseRouting,
    final: { ...baseRouting, initialAction: 'none', action: 'mask_external',
      toolOutputMasked: 1, toolOutputFindings: [finding('tool_output')] },
    badge: '도구 결과 1건 마스킹', english: '1 masked in tool results',
  },
  categories: {
    initial: baseRouting,
    final: { ...baseRouting, initialAction: 'none', action: 'mask_external',
      toolOutputMasked: 5, toolOutputFindings: [finding('tool_output'),
        { ...finding('tool_output'), count: 2 },
        { category: '__proto__', source: 'tool_output', count: 1 },
        { category: 'candidate_value_must_not_render', source: 'tool_output', count: 1 },
      ] },
    badge: '도구 결과 5건 마스킹', english: '5 masked in tool results',
  },
  input: {
    initial: { ...baseRouting, action: 'mask_external', findingCounts: [finding('current_input')] },
    final: { ...baseRouting, action: 'mask_external', findingCounts: [finding('current_input')] },
    badge: '사용자 입력을 가려 전송함', english: 'Sent with user input masked',
  },
  context: {
    initial: { ...baseRouting, action: 'mask_external', findingCounts: [finding('memory')] },
    final: { ...baseRouting, action: 'mask_external', findingCounts: [finding('memory')] },
    badge: '참고자료를 가려 전송함', english: 'Sent with reference context masked',
  },
  both: {
    initial: { ...baseRouting, action: 'mask_external',
      findingCounts: [finding('current_input'), finding('memory')] },
    final: { ...baseRouting, initialAction: 'mask_external', action: 'mask_external',
      findingCounts: [finding('current_input'), finding('memory')],
      toolOutputMasked: 1, toolOutputFindings: [finding('tool_output')] },
    badge: '요청·참고자료를 가려 전송함', english: 'Sent with request and reference context masked',
  },
  raw: {
    initial: { ...baseRouting, action: 'send_raw_external', findingCounts: [finding('current_input')] },
    final: { ...baseRouting, initialAction: 'send_raw_external', action: 'mask_external',
      findingCounts: [finding('current_input')], toolOutputMasked: 1,
      toolOutputFindings: [finding('tool_output')] },
    badge: '확인 후 요청 원문은 외부 전송', english: 'After confirmation, the original request goes outside',
  },
  strict: {
    initial: { ...baseRouting, action: 'strict_local', dataBoundary: 'self_hosted' },
    final: { ...baseRouting, initialAction: 'strict_local', action: 'strict_local',
      dataBoundary: 'self_hosted', toolOutputMasked: 0,
      toolOutputFindings: [finding('tool_output')] },
    badge: 'strict-local로 보호됨', english: 'Kept strict-local',
  },
  legacy: {
    initial: { ...baseRouting, action: 'mask_external' },
    final: { ...baseRouting, action: 'mask_external', toolOutputMasked: 1 },
    badge: '개인정보를 가려 전송함', english: 'Sent with personal data masked',
  },
  unknown: {
    initial: { ...baseRouting, action: 'mask_external', findingCounts: [finding('custom_context')] },
    final: { ...baseRouting, action: 'mask_external', findingCounts: [finding('custom_context')] },
    badge: '개인정보를 가려 전송함', english: 'Sent with personal data masked',
  },
} as const
type Scenario = keyof typeof scenarios

/** Only synthetic metadata is supplied; no original sensitive value exists in this fixture. */
async function mockMasking(page: Page, scenario: Scenario, saved: boolean, live = true) {
  const selected = scenarios[scenario]
  const unexpected: string[] = []
  const requests: Record<string, unknown>[] = []
  page.on('pageerror', (error) => unexpected.push(error.message))
  await page.route('https://**', async (route) => {
    unexpected.push('unexpected external request')
    await route.abort()
  })
  const messages = [
    { id: 'question', role: 'user', content: prompt, routing: selected.initial,
      attachments: [], createdAt: now },
    { id: 'answer', role: 'assistant', content: answer, routing: selected.final,
      model: 'fixture/model', usage: { inputTokens: 12, outputTokens: 8, credits: 0 },
      attachments: [], createdAt: now,
      steps: [{ id: 'search', label: '웹 검색', status: 'done', detail: '5개 결과 · 3개 본문 읽음' }] },
  ]
  const row = {
    id: sessionId, title: '도구 결과 마스킹 안내', kind: 'chat', model: 'fixture/model',
    routingMode: 'manual', projectId: null, agentId: null, artifactId: null, pinned: false,
    messages: saved ? messages : [], messageCount: saved ? 2 : 0, createdAt: now, updatedAt: now,
  }
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', email: 'fixture@example.test', name: '검증 사용자', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
        allowedModels: [], createdAt: now,
        preferences: { autoMemory: false, showUsage: true, streamResponses: live } },
    })
    if (path === '/auth/config') return json({ brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat'], privacy: { externalDataGuard: false },
      passwordResetEnabled: false, dictationEnabled: false })
    if (path === '/models') return json({ litellmAvailable: true, defaultChatModel: 'fixture/model',
      models: [{ id: 'fixture/model', label: '검증 모델', name: '검증 모델', provider: 'fixture',
        vendor: 'Fixture', kinds: ['chat'], modality: 'chat',
        dataBoundary: selected.final.dataBoundary, strictLocal: scenario === 'strict',
        privacyOnly: false, creditCost: 0, inputCreditCost: 0, supportsTools: true,
        supportsVision: false, contextWindow: 64000 }] })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && request.method() === 'GET') {
      // Do not let a list refresh hide a broken streaming metadata update.
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
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: [
          { type: 'privacy_route', ...selected.initial },
          { type: 'step', id: 'search', label: '웹 검색', status: 'done', detail: '5개 결과 · 3개 본문 읽음' },
          { type: 'privacy_route', ...selected.final },
          { type: 'delta', text: answer },
          { type: 'usage', inputTokens: 12, outputTokens: 8, credits: 0 },
          { type: 'done', messageId: 'answer' },
        ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') })
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
  return { unexpected, requests }
}

async function assertBadges(page: Page, scenario: Scenario) {
  await expect(page.getByText(answer, { exact: true })).toBeVisible()
  const details = page.getByRole('button', { name: /처리 내역 \d+건/ })
  if (await details.isVisible()) await details.click()
  await expect(page.getByText(scenarios[scenario].badge, { exact: true })).toBeVisible()
  if (scenario === 'tool') {
    await expect(page.getByText('개인정보를 가려 전송함', { exact: true })).toHaveCount(0)
    await expect(page.getByText(/사용자 입력을 가려|참고자료를 가려/)).toHaveCount(0)
    await expect(page.getByText('도구 결과 1건 마스킹', { exact: true })).toHaveAttribute(
      'title', '민감정보 후보로 탐지된 부분을 도구 결과에서 가렸습니다. API 키 1건',
    )
  }
  if (scenario === 'categories') {
    await expect(page.getByText(scenarios.categories.badge, { exact: true })).toHaveAttribute(
      'title', '민감정보 후보로 탐지된 부분을 도구 결과에서 가렸습니다. API 키 3건 · 기타 민감정보 2건',
    )
    await expect(page.locator('body')).not.toContainText(/__proto__|candidate_value_must_not_render/)
  }
  if (scenario === 'both' || scenario === 'raw' || scenario === 'legacy') {
    await expect(page.getByText('도구 결과 1건 마스킹', { exact: true })).toBeVisible()
  }
  if (scenario === 'strict') {
    await expect(page.getByText(/도구 결과 \d+건 마스킹|가려 전송함/)).toHaveCount(0)
  } else {
    await expect(page.getByText('외부 전환 가능', { exact: true })).toHaveAttribute(
      'title', '모델 설정상의 데이터 처리 경계입니다. 이번 요청에서 외부 모델이 실행됐다는 뜻은 아닙니다.',
    )
  }
  await expect(page.getByText('custom_context', { exact: false })).toHaveCount(0)
  const badges = page.getByText(scenarios[scenario].badge, { exact: true })
  expect(await badges.evaluate((node) => {
    const box = node.getBoundingClientRect()
    const parent = node.parentElement!.getBoundingClientRect()
    return box.left >= parent.left && box.right <= parent.right + 1
  })).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0)
}

for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 }]) {
  for (const mode of ['saved', 'streamed'] as const) {
    test(`${viewport.name} ${mode}: 도구만 마스킹한 경우 요청 마스킹으로 표시하지 않는다`, async ({ page }) => {
      await page.setViewportSize(viewport)
      const state = await mockMasking(page, 'tool', mode === 'saved')
      await page.goto(`/s/${sessionId}`)
      if (mode === 'streamed') {
        await page.getByLabel('프롬프트 입력').fill(prompt)
        await page.getByLabel('프롬프트 입력').press('Enter')
      }
      await expect(page.getByText(answer, { exact: true })).toBeVisible()
      await assertBadges(page, 'tool')
      if (process.env.SEARCH_MASKING_SCREENSHOT_DIR && mode === 'streamed') {
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({ path: `${process.env.SEARCH_MASKING_SCREENSHOT_DIR}/search-masking-${viewport.name}.png`, animations: 'disabled' })
      }
      await page.reload()
      await assertBadges(page, 'tool')
      await page.getByRole('button', { name: '언어 전환 · EN', exact: true }).click()
      await expect(page.getByText(scenarios.tool.english, { exact: true })).toBeVisible()
      await expect(page.getByText('Sent with personal data masked', { exact: true })).toHaveCount(0)
      expect(state.requests.map((request) => request.content)).toEqual(mode === 'saved' ? [] : [prompt])
      expect(state.unexpected).toEqual([])
    })
  }
}

for (const scenario of ['input', 'context', 'both', 'raw', 'strict', 'legacy', 'unknown', 'categories'] as const) {
  test(`${scenario}: 출처와 strict-local을 구분하고 원문을 노출하지 않는다`, async ({ page }) => {
    const state = await mockMasking(page, scenario, true)
    await page.goto(`/s/${sessionId}`)
    await assertBadges(page, scenario)
    await page.getByRole('button', { name: '언어 전환 · EN', exact: true }).click()
    await expect(page.getByText(scenarios[scenario].english, { exact: true })).toBeVisible()
    if (scenario === 'categories') {
      await expect(page.getByText(scenarios.categories.english, { exact: true })).toHaveAttribute(
        'title', 'Potentially sensitive parts were masked in tool results. API keys: 3 · Other sensitive data: 2',
      )
    }
    expect(state.unexpected).toEqual([])
  })
}

test('스트리밍 표시를 꺼도 도구 마스킹 출처를 보존한다', async ({ page }) => {
  const state = await mockMasking(page, 'tool', false, false)
  await page.goto(`/s/${sessionId}`)
  await page.getByLabel('프롬프트 입력').fill(prompt)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page.getByText(answer, { exact: true })).toBeVisible()
  await assertBadges(page, 'tool')
  expect(state.unexpected).toEqual([])
})
