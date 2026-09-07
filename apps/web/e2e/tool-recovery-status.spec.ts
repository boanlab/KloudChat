import { expect, test, type Page } from '@playwright/test'

/** Synthetic read-tool retries through the real chat UI; no backend or model calls. */
const sessionId = 'tool-recovery-status'
const now = '2026-09-08T00:00:00.000Z'
const answer = '도서관 운영 시간을 다시 확인했습니다. 평일에는 오전 9시에 엽니다.'
const firstRead = {
  id: 'read-1', type: 'tool', label: '운영 시간 조회', status: 'error',
  detail: '일시적인 조회 오류',
}
const retryRead = {
  id: 'read-2', type: 'tool', label: '운영 시간 재조회', status: 'done',
}
type Row = Record<string, unknown>

function assistant(overrides: Row = {}): Row {
  return {
    id: 'answer-1', role: 'assistant', content: answer, createdAt: now,
    steps: [firstRead, retryRead], failure: null, model: 'mock/read-model',
    ...overrides,
  }
}

async function mockChat(page: Page, messages: Row[]) {
  const unexpected: string[] = []
  page.on('pageerror', (error) => unexpected.push(`pageerror: ${error.message}`))
  const row = {
    id: sessionId, kind: 'chat', title: '도서관 운영 시간', projectId: null,
    agentId: null, model: 'mock/read-model', routingMode: 'manual', artifactId: null,
    pinned: false, createdAt: now, updatedAt: now, messages,
    preview: '도서관 운영 시간을 확인해 줘.', messageCount: messages.length,
  }
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, '')
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'mock-access-token', expiresIn: 3600,
      user: {
        id: 'tool-recovery-user', email: 'reader@example.com', name: '도서관 이용자',
        role: 'user', status: 'active', monthlyCredits: 1000, creditsUsed: 0,
        cycleResetsAt: null, avatarColor: '#64748b', litellmKeyPreview: null,
        litellmKeyIssuedAt: null, allowedModels: [], createdAt: now, lastActiveAt: now,
        preferences: { streamResponses: true, autoMemory: false, showUsage: false },
      },
    })
    if (path === '/auth/config') return json({
      passwordResetEnabled: false, dictationEnabled: false,
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: false, allowUserRawExternal: false },
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'mock/read-model',
      models: [{
        id: 'mock/read-model', label: 'Mock · Read Model', name: 'Read Model',
        vendor: 'Mock', provider: 'mock', modality: 'chat', kinds: ['chat'],
        creditCost: 0, inputCreditCost: 0, supportsVision: false, supportsTools: true,
      }],
    })
    if (path === '/sessions' && route.request().method() === 'GET') {
      return json([{ ...row, messages: null }])
    }
    if (path === `/sessions/${sessionId}`) return json(row)
    if (path === `/sessions/${sessionId}/stop`) return json({ stopped: true })
    if (path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    if ([
      '/tools', '/skills', '/projects', '/artifacts', '/memory', '/agents',
      '/connectors', '/connectors/catalog', '/templates', '/jobs',
      '/designs', '/design-templates', '/prompt-templates', '/shares',
      `/sessions/${sessionId}/jobs`,
    ].includes(path)) return json([])
    unexpected.push(`${route.request().method()} ${path}`)
    return route.fulfill({ status: 501, json: { detail: 'Unmocked QA request' } })
  })
  await page.goto(`/s/${sessionId}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  return unexpected
}

function timeline(page: Page) {
  return page.getByRole('button', { expanded: false }).filter({ hasText: /작업 완료|중단됨/ })
}

test('재조회 후 답변을 마친 대화는 완료로 표시하고 이전 오류는 펼쳐서 볼 수 있다', async ({ page }, testInfo) => {
  const unexpected = await mockChat(page, [assistant()])
  const head = timeline(page)
  await expect(head).toContainText('작업 완료')
  await expect(head).not.toContainText('중단됨')
  await expect(page.getByText(answer)).toBeVisible()
  await expect(head).toBeInViewport()
  if (process.env.TOOL_RECOVERY_SCREENSHOT_DIR) {
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({
      path: `${process.env.TOOL_RECOVERY_SCREENSHOT_DIR}/recovered-collapsed-${testInfo.project.name}.png`,
      animations: 'disabled',
    })
  }
  await head.click()
  await expect(page.getByText('일시적인 조회 오류')).toBeVisible()
  await expect(page.getByText('운영 시간 재조회', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '다시 시도', exact: true })).toHaveCount(0)
  if (process.env.TOOL_RECOVERY_SCREENSHOT_DIR) {
    await page.screenshot({
      path: `${process.env.TOOL_RECOVERY_SCREENSHOT_DIR}/recovered-expanded-${testInfo.project.name}.png`,
      animations: 'disabled',
    })
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  expect(unexpected).toEqual([])
})

for (const failure of ['interrupted', 'stopped'] as const) {
  test(`${failure}: 도구가 성공했어도 답변이 끝나지 않았으면 중단으로 표시한다`, async ({ page }) => {
    const unexpected = await mockChat(page, [assistant({ steps: [retryRead], failure })])
    await expect(timeline(page)).toContainText('중단됨')
    await expect(timeline(page)).not.toContainText('작업 완료')
    await expect(page.getByText(failure === 'stopped'
      ? '여기서 멈췄습니다.' : '답변이 중간에 끊겨 여기까지만 남았습니다.')).toBeVisible()
    expect(unexpected).toEqual([])
  })
}

/** Keep the SSE response open after a failed read and a running retry. */
async function holdRetryStream(page: Page, end: 'success' | 'failure') {
  await page.addInitScript(({ firstRead, retryRead, answer, end }) => {
    const originalFetch = window.fetch.bind(window)
    const control = window as typeof window & { finishReadRetry?: () => void }
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/messages') || init?.method !== 'POST') return originalFetch(input, init)
      const encoder = new TextEncoder()
      const stream = new ReadableStream({
        start(controller) {
          const emit = (event: Record<string, unknown>) =>
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
          emit({ ...firstRead, type: 'step', category: 'tool' })
          emit({ ...retryRead, type: 'step', category: 'tool', status: 'running' })
          control.finishReadRetry = () => {
            emit({ ...retryRead, type: 'step', category: 'tool' })
            if (end === 'success') {
              emit({ type: 'delta', text: answer })
              emit({ type: 'usage', inputTokens: 10, outputTokens: 10, credits: 0 })
            } else {
              emit({ type: 'error', message: '답변 전송이 중단되었습니다.' })
            }
            emit({ type: 'done' })
            controller.close()
          }
          init?.signal?.addEventListener('abort', () => {
            controller.error(new DOMException('Aborted', 'AbortError'))
          }, { once: true })
        },
      })
      return new Response(stream, { headers: { 'content-type': 'text/event-stream' } })
    }
  }, { firstRead, retryRead, answer, end })
}

for (const end of ['success', 'failure', 'stop'] as const) {
  test(`재조회 진행 중은 작업 중이며 ${end} 종료 상태를 구별한다`, async ({ page }) => {
    await holdRetryStream(page, end === 'stop' ? 'success' : end)
    const unexpected = await mockChat(page, [])
    await page.getByLabel('프롬프트 입력').fill('도서관 운영 시간을 확인해 줘.')
    await page.getByLabel('프롬프트 입력').press('Enter')
    const liveHead = page.getByRole('button', { expanded: true }).filter({ hasText: '작업 중' })
    await expect(liveHead).toBeVisible()
    await expect(page.getByText('일시적인 조회 오류')).toBeVisible()
    await expect(page.getByText('운영 시간 재조회', { exact: false })).toBeVisible()
    await expect(page.getByText('중단됨', { exact: true })).toHaveCount(0)
    if (end === 'stop') {
      await page.getByLabel('중지', { exact: true }).click()
    } else {
      await page.evaluate(() => {
        const control = window as typeof window & { finishReadRetry?: () => void }
        control.finishReadRetry!()
      })
    }
    await expect(timeline(page)).toContainText(end === 'success' ? '작업 완료' : '중단됨')
    if (end === 'success') await expect(page.getByText(answer)).toBeVisible()
    if (end === 'failure') await expect(page.getByText('답변 전송이 중단되었습니다.')).toBeVisible()
    if (end === 'stop') await expect(page.getByText('여기서 멈췄습니다.')).toBeVisible()
    expect(unexpected).toEqual([])
  })
}
