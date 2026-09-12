import { expect, test, type Page } from '@playwright/test'

const sessionId = 'legacy-freshness-fixture'
const now = '2026-09-12T00:00:00.000Z'
const answer = '최신 정보를 확인할 수 없어 답변을 보류합니다. 확인 가능한 자료를 제공해 주세요.'

/** Historical transcript compatibility, not the policy for new requests. */
async function savedLegacyAnswer(page: Page, reason: string) {
  const unexpected: string[] = []
  const routing = { answerOrigin: 'server_policy', actualModel: null,
    freshness: { status: 'unverified', reason } }
  const row = {
    id: sessionId, title: '이전 정책 답변', kind: 'chat', model: 'fixture/qwen',
    routingMode: 'manual', projectId: null, agentId: null, artifactId: null, pinned: false,
    messages: [
      { id: 'question', role: 'user', content: '저장된 과거 질문', createdAt: now },
      { id: 'answer', role: 'assistant', content: answer, model: null, routing,
        usage: { inputTokens: 0, outputTokens: 0, credits: 0 }, failure: null, createdAt: now },
    ], messageCount: 2, createdAt: now, updatedAt: now,
  }
  await page.route('**/api/**', async (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname.slice(4)
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', email: 'fixture@example.test', name: 'Fixture', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
        allowedModels: [], createdAt: now,
        preferences: { autoMemory: false, showUsage: true, streamResponses: true } } })
    if (path === '/auth/config') return json({ brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat'], privacy: { externalDataGuard: false }, dictationEnabled: false })
    if (path === '/models') return json({ models: [{ id: 'fixture/qwen', label: 'Qwen 검증 모델',
      name: 'Qwen', vendor: 'Fixture', provider: 'fixture', kinds: ['chat'], modality: 'chat',
      dataBoundary: 'self_hosted', strictLocal: true, creditCost: 0, inputCreditCost: 0 }],
      litellmAvailable: true, defaultChatModel: 'fixture/qwen' })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (req.method() === 'GET' && path === '/sessions') return json([row])
    if (req.method() === 'GET' && path === `/sessions/${sessionId}`) return json(row)
    if (path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    if (req.method() === 'GET' && (path.endsWith('/jobs') || [
      '/projects', '/artifacts', '/skills', '/memory', '/agents', '/tools', '/templates',
      '/connectors', '/connectors/catalog', '/designs', '/design-templates', '/prompt-templates', '/shares',
    ].includes(path))) return json([])
    unexpected.push(`${req.method()} ${path}`)
    return route.fulfill({ status: 501, json: { detail: 'Unmocked fixture request' } })
  })
  return unexpected
}

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 },
]) {
  for (const reason of ['verification_unavailable', 'lookup_failed_or_empty']) {
    test(`${viewport.name} legacy saved ${reason}: 이전 서버 정책 기록의 출처와 비용을 유지한다`, async ({ page }) => {
      await page.setViewportSize(viewport)
      const unexpected = await savedLegacyAnswer(page, reason)
      await page.goto(`/s/${sessionId}`)
      await expect(page.getByText(answer, { exact: true })).toBeVisible()
      await expect(page.getByText('서비스 정책 안내 · 최신 정보 검증 불가 · 모델 실행 없음', { exact: true })).toBeVisible()
      await expect(page.getByText('모델 실행 없음 · 0 in · 0 out · 0 크레딧', { exact: true })).toBeVisible()
      await page.reload()
      await expect(page.getByText(answer, { exact: true })).toBeVisible()
      await page.getByRole('button', { name: '언어 전환 · EN', exact: true }).click()
      await expect(page.getByText('Service policy notice · Current information unverified · No model execution', { exact: true })).toBeVisible()
      expect(unexpected).toEqual([])
    })
  }
}
