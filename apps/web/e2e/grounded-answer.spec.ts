import { expect, test, type Page, type TestInfo } from '@playwright/test'

test.use({ serviceWorkers: 'block' })

const id = 'grounded-answer-fixture'
const at = '2026-09-12T00:00:00.000Z'
const selected = 'fixture/qwen'
const fallback = 'fixture/fallback'
const koUnknown = '다만 이 답변은 부정확할 수 있으며, 이 모델의 학습 기준 연월을 확인할 수 없어 최신 사실은 별도 검증이 필요합니다.'
const koKnown = '다만 이 모델의 학습 기준은 2025년 1월로 제공되어 이후의 변화가 반영되지 않았거나 답변이 부정확할 수 있으므로 최신 사실은 별도 검증이 필요합니다.'
const enUnknown = "However, this answer may be inaccurate; this model's training cutoff could not be verified, so current facts need independent verification."
const enKnown = "However, the model catalogue lists this model's training cutoff as 2025-01, so later changes may be missing and this answer may be inaccurate; current facts need independent verification."
const domains = [
  { name: 'politics', prompt: '웹 검색 없이 현재 대한민국 대통령이 누구인지 답해줘.', answer: '합성 검증 자료의 현직 인물은 Fixture Person으로 표시됩니다.', lang: 'ko' },
  { name: 'science', prompt: '과학에서 관측과 가설은 어떤 관계야?', answer: '가설은 관측으로 검토할 설명이며, 새로운 관측에 따라 수정될 수 있습니다.', lang: 'ko' },
  { name: 'software', prompt: '소프트웨어 버전 호환성을 확인하는 방법은?', answer: '사용 중인 버전과 의존성의 지원 범위를 확인하고 해당 조합을 테스트합니다.', lang: 'ko' },
  { name: 'finance', prompt: '일반적으로 예산과 실제 지출을 어떻게 비교해?', answer: '같은 기간과 항목으로 예산과 실제 지출을 나란히 정리해 차이를 비교합니다.', lang: 'ko' },
  { name: 'english', prompt: 'Explain the difference between a model and an observation.', answer: 'A model represents an explanation, while an observation records what was measured.', lang: 'en' },
] as const
type Domain = typeof domains[number]
type Cutoff = 'known' | 'unknown' | 'fallback'

async function fixture(page: Page, info: TestInfo, domain: Domain, cutoff: Cutoff, saved = false) {
  await page.addInitScript((language) => localStorage.setItem('kchat-lang', language), domain.lang)
  const origin = new URL(String(info.project.use.baseURL)).origin
  const actual = cutoff === 'fallback' ? fallback : selected
  const label = actual === fallback ? 'Fallback fixture' : 'Qwen fixture'
  const known = cutoff === 'known'
  const footer = domain.lang === 'ko' ? (known ? koKnown : koUnknown) : (known ? enKnown : enUnknown)
  const routing = { requestedModels: [selected], routedModels: [selected], effectiveModels: [selected],
    actualModels: [actual], actualModel: actual, action: 'none', dataBoundary: 'self_hosted',
    accuracy: { policy: 'grounded-best-effort-v1', knowledgeCutoff: known ? '2025-01' : null,
      cutoffSource: known ? 'model_catalogue' : 'unknown' } }
  const complete = [
    { id: 'fixture-question', role: 'user', content: domain.prompt, createdAt: at },
    { id: 'fixture-answer', role: 'assistant', content: `${domain.answer}\n\n${footer}`,
      model: actual, routing, usage: { inputTokens: 12, outputTokens: 34, credits: 5 },
      failure: null, attachments: [], createdAt: at },
  ]
  const row = { id, title: 'Grounded answer fixture', kind: 'chat', model: selected, routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pinned: false,
    messages: saved ? complete : [], messageCount: saved ? 2 : 0, createdAt: at, updatedAt: at }
  const requests: { path: string; status: number; body: Record<string, unknown> }[] = []
  const unexpected: string[] = []
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) {
      unexpected.push(`external: ${url.origin}`)
      return route.abort('blockedbyclient')
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: 'Fixture', email: 'fixture@example.test', role: 'user', status: 'active',
        monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
        preferences: { autoMemory: false, showUsage: true, streamResponses: true } } })
    if (path === '/auth/config') return json({ brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false })
    if (path === '/models') return json({ models: [selected, fallback].map((modelId) => ({
      id: modelId, name: modelId, label: modelId === fallback ? 'Fallback fixture' : 'Qwen fixture',
      vendor: 'Fixture', provider: 'fixture', kinds: ['chat'], modality: 'chat', strictLocal: true,
      dataBoundary: 'self_hosted', privacyOnly: false, supportsTools: true, contextWindow: 64000,
      creditCost: 5, inputCreditCost: 1,
      knowledgeCutoff: modelId === fallback ? '2024-10' : cutoff === 'unknown' ? null : '2025-01',
    })), defaultChatModel: selected, litellmAvailable: true })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 5, creditsRemaining: 995 })
    if (request.method() === 'GET' && path === '/sessions') return json([row])
    if (request.method() === 'GET' && path === `/sessions/${id}`) return json(row)
    if (request.method() === 'POST' && path === `/sessions/${id}/messages`) {
      requests.push({ path, status: 200, body: request.postDataJSON() })
      row.messages = complete
      row.messageCount = 2
      return route.fulfill({ contentType: 'text/event-stream', body: [
        { type: 'privacy_route', ...routing },
        { type: 'delta', text: domain.answer },
        { type: 'delta', text: `\n\n${footer}` },
        { type: 'usage', inputTokens: 12, outputTokens: 34, credits: 5 },
        { type: 'done', messageId: 'fixture-answer' },
      ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') })
    }
    if (path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    if (request.method() === 'GET' && (path.endsWith('/jobs') || [
      '/projects', '/artifacts', '/skills', '/memory', '/agents', '/tools', '/templates',
      '/connectors', '/connectors/catalog', '/designs', '/design-templates', '/prompt-templates', '/shares',
    ].includes(path))) return json([])
    unexpected.push(`${request.method()} ${path}`)
    return route.fulfill({ status: 501, json: { detail: 'Unmocked fixture request' } })
  })
  return { row, requests, unexpected, footer, label }
}

async function checkAnswer(page: Page, domain: Domain, state: Awaited<ReturnType<typeof fixture>>) {
  await expect(page.getByText(domain.answer, { exact: true })).toBeVisible()
  const footer = page.getByText(state.footer, { exact: true })
  await expect(footer).toBeVisible()
  await expect(footer).toHaveCount(1)
  await footer.scrollIntoViewIfNeeded()
  await expect(footer).toBeInViewport()
  await expect(page.getByText(new RegExp(`${state.label} · 12 in · 34 out · 5`))).toBeVisible()
  await expect(page.getByText(/서비스 정책 안내|Service policy notice|모델 실행 없음|No model execution/)).toHaveCount(0)
  await expect(page.getByText(/답변을 보류합니다|freshness_verification_unavailable/)).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0)
}

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 },
]) {
  for (const cutoff of ['known', 'unknown', 'fallback'] as const) {
    for (const domain of domains) {
      test(`${viewport.name} ${domain.name} ${cutoff}: 답변 후 주의문과 모델 출처를 유지한다`, async ({ page }, info) => {
        await page.setViewportSize(viewport)
        const state = await fixture(page, info, domain, cutoff)
        await page.goto(`/s/${id}`)
        const input = page.getByLabel(domain.lang === 'ko' ? '프롬프트 입력' : 'Prompt', { exact: true })
        await input.fill(domain.prompt)
        await input.press('Enter')
        await checkAnswer(page, domain, state)
        expect(state.requests).toEqual([{ path: `/sessions/${id}/messages`, status: 200,
          body: expect.objectContaining({ content: domain.prompt }) }])
        expect(state.row.messages[1].content).toBe(`${domain.answer}\n\n${state.footer}`)
        await page.reload()
        await checkAnswer(page, domain, state)
        if (cutoff !== 'known') await expect(page.getByText(koKnown, { exact: true })).toHaveCount(0)
        expect(state.unexpected).toEqual([])
        if (process.env.GROUNDED_SCREENSHOT_DIR && domain.name === 'politics') {
          await page.evaluate(() => document.fonts.ready)
          await page.screenshot({ path: `${process.env.GROUNDED_SCREENSHOT_DIR}/grounded-${cutoff}-${viewport.name}.png`, animations: 'disabled' })
        }
      })
    }
    test(`${viewport.name} saved ${cutoff}: 기존 일반 답변의 주의문을 다시 생성하지 않는다`, async ({ page }, info) => {
      await page.setViewportSize(viewport)
      const domain = domains[0]
      const state = await fixture(page, info, domain, cutoff, true)
      await page.goto(`/s/${id}`)
      await checkAnswer(page, domain, state)
      expect(state.requests).toEqual([])
      expect(state.unexpected).toEqual([])
    })
  }
}
