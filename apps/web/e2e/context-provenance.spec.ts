import { expect, test, type Page } from '@playwright/test'

/**
 * What the turn was given, on screen where the turn happened.
 *
 * Memories, attached files and project knowledge never appear in the
 * conversation, so an answer built on half of an uploaded document looked
 * exactly like an answer built on all of it. The server now sends one quiet
 * timeline line per source, and this suite is about whether a person can read
 * them: the names of the memories without their bodies, the file that was cut
 * by name, and the memory the turn saved on its way out.
 *
 * Fully mocked — the subject is the timeline, and a paid completion must not
 * decide whether it passes.
 */

const now = '2026-08-18T00:00:00.000Z'
const sessionId = 'cccccccccccccccccccccccccccccccc'

/** The stream a turn with a full context produces, in the order it arrives. */
const events = [
  {
    type: 'step',
    category: 'thinking',
    id: 'context-memories',
    label: '메모리 2건 참고',
    status: 'done',
    detail: '말투 · 소속',
    memories: ['말투', '소속'],
  },
  {
    type: 'step',
    category: 'thinking',
    id: 'context-attachments',
    label: '첨부 2개 중 1개 잘림',
    status: 'done',
    detail: '부록.pdf 8,000자만 반영',
  },
  {
    type: 'step',
    category: 'thinking',
    id: 'context-knowledge',
    label: '프로젝트 지식 3개 중 1개 빠짐',
    status: 'done',
    detail: '연혁.md 분량을 넘겨 제외',
  },
  { type: 'delta', text: '요약했습니다.' },
  { type: 'step', id: 'tool-1', label: '웹 검색', status: 'done' },
  // Last, as the server sends it: auto-memory runs after the answer is stored.
  {
    type: 'step',
    category: 'thinking',
    id: 'memory-saved',
    label: '메모리 1건 저장',
    status: 'done',
    detail: '자동 메모리에 추가됨',
  },
  { type: 'usage', inputTokens: 20, outputTokens: 4, credits: 0 },
]

async function mockChat(page: Page) {
  await page.route(/\/api\/auth\/refresh$/, (route) =>
    route.fulfill({
      json: {
        accessToken: 'mock-access-token',
        expiresIn: 3_600,
        user: {
          id: '88888888-8888-4888-8888-888888888888',
          email: 'context-provenance@example.com',
          name: '출처 확인',
          role: 'user',
          status: 'active',
          monthlyCredits: 1_000,
          creditsUsed: 0,
          cycleResetsAt: null,
          avatarColor: '#64748b',
          litellmKeyPreview: null,
          litellmKeyIssuedAt: null,
          preferences: { streamResponses: true, autoMemory: true, showUsage: true },
          allowedModels: [],
          createdAt: now,
          lastActiveAt: now,
        },
      },
    }),
  )
  await page.route(/\/api\/auth\/config$/, (route) =>
    route.fulfill({
      json: {
        passwordResetEnabled: false,
        dictationEnabled: false,
        brand: { name: 'KloudChat', logo: '' },
        enabledKinds: ['chat', 'report', 'slides', 'image', 'av'],
      },
    }),
  )
  await page.route(/\/api\/models$/, (route) =>
    route.fulfill({
      json: {
        litellmAvailable: true,
        defaultChatModel: 'mock/tool-model',
        models: [
          {
            id: 'mock/tool-model',
            label: 'Mock · Tool Model',
            name: 'Tool Model',
            vendor: 'Mock',
            provider: 'mock',
            modality: 'chat',
            kinds: ['chat', 'report', 'slides'],
            creditCost: 0,
            inputCreditCost: 0,
            supportsVision: false,
            supportsTools: true,
          },
        ],
      },
    }),
  )
  for (const endpoint of [
    /\/api\/tools(?:\?.*)?$/,
    /\/api\/skills(?:\?.*)?$/,
    /\/api\/projects(?:\?.*)?$/,
    /\/api\/artifacts(?:\?.*)?$/,
    /\/api\/memory(?:\?.*)?$/,
    /\/api\/agents(?:\?.*)?$/,
    /\/api\/connectors(?:\?.*)?$/,
    /\/api\/connectors\/catalog(?:\?.*)?$/,
  ]) {
    await page.route(endpoint, (route) => route.fulfill({ json: [] }))
  }

  const sessions: Record<string, unknown>[] = []
  await page.route(/\/api\/sessions(?:\?.*)?$/, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: sessions })
      return
    }
    if (route.request().method() !== 'POST') return route.continue()
    const request = route.request().postDataJSON() as { kind: string; model?: string }
    const row = {
      id: sessionId,
      kind: request.kind,
      title: '',
      projectId: null,
      agentId: null,
      model: request.model ?? 'mock/tool-model',
      artifactId: null,
      pinned: false,
      createdAt: now,
      updatedAt: now,
      messages: null,
      preview: null,
      messageCount: 0,
    }
    sessions.splice(0, sessions.length, row)
    await route.fulfill({ status: 201, json: row })
  })
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
    })
  })
}

async function answerOnce(page: Page) {
  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('출처 확인')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page.getByText('요약했습니다.')).toBeVisible({ timeout: 15_000 })
}

test('턴이 받은 메모리·첨부·프로젝트 지식이 타임라인 한 줄씩으로 남는다', async ({ page }) => {
  await mockChat(page)
  await answerOnce(page)

  const timeline = page.getByRole('button').filter({ hasText: '메모리 1건 저장' })
  await expect(timeline).toBeVisible({ timeout: 15_000 })
  await timeline.click()

  // Named, not counted: which document was cut is the whole point of the line.
  await expect(page.getByText('부록.pdf 8,000자만 반영')).toBeVisible()
  await expect(page.getByText('연혁.md 분량을 넘겨 제외')).toBeVisible()
  await expect(page.getByText('메모리 2건 참고')).toBeVisible()
  // The memories arrive as names. A body on screen would make every turn a
  // disclosure, which is exactly what the memory drawer exists to avoid.
  await expect(page.getByText('말투 · 소속')).toBeVisible()
})

test('접힌 타임라인은 받은 것만 나열하다 끝나지 않는다', async ({ page }) => {
  await mockChat(page)
  await answerOnce(page)

  const timeline = page.getByRole('button').filter({ hasText: '메모리 1건 저장' })
  await expect(timeline).toBeVisible({ timeout: 15_000 })
  // Five steps, three named and the rest counted — otherwise the folded line
  // spends itself on provenance and never reaches the work.
  await expect(timeline).toContainText('외 1개')
  await expect(timeline).toContainText('5단계')
})

test('자동 메모리가 쓴 기록은 그 턴 안에서 보인다', async ({ page }) => {
  await mockChat(page)
  await answerOnce(page)

  const timeline = page.getByRole('button').filter({ hasText: '메모리 1건 저장' })
  await timeline.click()
  await expect(page.getByText('자동 메모리에 추가됨')).toBeVisible()
})
