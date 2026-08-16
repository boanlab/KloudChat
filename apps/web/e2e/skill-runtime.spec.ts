import { expect, test, type Page } from '@playwright/test'

/**
 * Skill-runtime regressions use a fixed catalogue and a fake stream. The
 * affordances are the subject here; a changing starter catalogue or a paid
 * completion must not decide whether these tests pass.
 */

type SkillRow = {
  id: string
  name: string
  slug: string
  description: string
  whenToUse: string
  body: string
  catalogKey: string | null
  source: 'built-in' | 'personal'
  kinds: string[]
  requiredTools: string[]
  estimatedTokens: number
  version: string
  enabled: boolean
  updatedAt: string
}

const now = '2026-08-16T00:00:00.000Z'
const sessionsCollection = /\/api\/sessions(?:\?.*)?$/

const skill = (
  id: string,
  name: string,
  options: Partial<Pick<SkillRow, 'body' | 'catalogKey' | 'requiredTools' | 'source'>> = {},
): SkillRow => ({
  id,
  name,
  slug: name.replaceAll(' ', '-'),
  description: `${name} 설명`,
  whenToUse: `${name} 절차가 필요할 때`,
  body: options.body ?? `${name}의 원본 절차를 순서대로 따른다.`,
  catalogKey: options.catalogKey ?? null,
  source: options.source ?? 'built-in',
  kinds: ['chat', 'report', 'slides'],
  requiredTools: options.requiredTools ?? [],
  estimatedTokens: name === '의사결정 메모' ? 123 : 80,
  version: '1.0.0',
  enabled: true,
  updatedAt: now,
})

const catalogue: SkillRow[] = [
  skill('11111111-1111-4111-8111-111111111111', '초안 구조화'),
  skill('22222222-2222-4222-8222-222222222222', '사실 확인'),
  skill('33333333-3333-4333-8333-333333333333', '의사결정 메모', {
    catalogKey: 'decision-memo',
  }),
  skill('44444444-4444-4444-8444-444444444444', '독자별 리스크 검토'),
  skill('55555555-5555-4555-8555-555555555555', '계산·단위 검증', {
    catalogKey: 'calculation-unit-verification',
    requiredTools: ['execute_code'],
  }),
  skill('66666666-6666-4666-8666-666666666666', '편집 보존 확인', {
    body: '1. 입력을 확인한다.\n2. 근거와 결론을 분리한다.\n3. 미확인 사항을 표시한다.',
    source: 'personal',
  }),
]

async function mockSkillWorkspace(page: Page, initial = catalogue) {
  let rows = initial.map((row) => ({ ...row }))
  let lastPatch: Record<string, unknown> | null = null

  await page.route(/\/api\/auth\/refresh$/, (route) =>
    route.fulfill({
      json: {
        accessToken: 'mock-access-token',
        expiresIn: 3_600,
        user: {
          id: '77777777-7777-4777-8777-777777777777',
          email: 'skill-runtime@example.com',
          name: '런타임 검증',
          role: 'user',
          status: 'active',
          monthlyCredits: 1_000,
          creditsUsed: 0,
          cycleResetsAt: null,
          avatarColor: '#64748b',
          litellmKeyPreview: null,
          litellmKeyIssuedAt: null,
          preferences: { streamResponses: true, autoMemory: false, showUsage: true },
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
          {
            id: 'mock/second-model',
            label: 'Mock · Second Model',
            name: 'Second Model',
            vendor: 'Mock',
            provider: 'mock',
            modality: 'chat',
            kinds: ['chat'],
            creditCost: 0,
            inputCreditCost: 0,
            supportsVision: false,
            supportsTools: true,
          },
        ],
      },
    }),
  )
  await page.route(/\/api\/tools(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: [{ name: 'execute_code', label: '코드 실행', available: true }],
    }),
  )
  await page.route(/\/api\/skills(?:\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    await route.fulfill({ json: rows })
  })
  await page.route(/\/api\/skills\/[^/?]+$/, async (route) => {
    if (route.request().method() !== 'PATCH') return route.continue()
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!
    lastPatch = (route.request().postDataJSON() ?? {}) as Record<string, unknown>
    rows = rows.map((row) =>
      row.id === id ? { ...row, ...lastPatch, updatedAt: '2026-08-16T01:00:00.000Z' } : row,
    )
    await route.fulfill({ json: rows.find((row) => row.id === id) })
  })
  for (const endpoint of [
    /\/api\/projects(?:\?.*)?$/,
    /\/api\/artifacts(?:\?.*)?$/,
    /\/api\/memory(?:\?.*)?$/,
    /\/api\/agents(?:\?.*)?$/,
    /\/api\/connectors(?:\?.*)?$/,
    /\/api\/connectors\/catalog(?:\?.*)?$/,
  ]) {
    await page.route(endpoint, (route) => route.fulfill({ json: [] }))
  }
  await page.route(sessionsCollection, (route) => route.fulfill({ json: [] }))

  return {
    rows: () => rows,
    lastPatch: () => lastPatch,
  }
}

async function openSkillMenu(page: Page) {
  await page.getByRole('button', { name: '스킬', exact: true }).click()
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible()
  return menu
}

async function selectSkill(page: Page, name: string) {
  const menu = await openSkillMenu(page)
  const item = menu.getByRole('menuitemcheckbox').filter({ hasText: name })
  await expect(item).toBeEnabled()
  await item.click()
}

test('입력창은 스킬을 세 개까지만 고르고 네 번째 이유를 설명한다', async ({ page }) => {
  await mockSkillWorkspace(page)
  await page.goto('/new/chat')

  await selectSkill(page, '초안 구조화')
  await selectSkill(page, '사실 확인')
  await selectSkill(page, '의사결정 메모')

  const menu = await openSkillMenu(page)
  const fourth = menu.getByRole('menuitemcheckbox').filter({ hasText: '독자별 리스크 검토' })
  await expect(fourth).toBeDisabled()
  await expect(fourth).toContainText('최대 3개까지 선택할 수 있습니다.')
  await expect(page.getByRole('button', { name: / 제거$/ })).toHaveCount(3)
})

test('도구가 필요한 스킬은 보고서와 비교 모드에서 이유와 함께 비활성화된다', async ({
  page,
}) => {
  await mockSkillWorkspace(page)

  await page.goto('/new/report')
  let menu = await openSkillMenu(page)
  let calculation = menu.getByRole('menuitemcheckbox').filter({ hasText: '계산·단위 검증' })
  await expect(calculation).toBeDisabled()
  await expect(calculation).toContainText(
    '이 화면에서는 도구가 필요한 스킬을 실행할 수 없습니다.',
  )

  await page.goto('/new/chat')
  await page.getByRole('button', { name: '모델 비교', exact: true }).click()
  await page.getByRole('menuitem').filter({ hasText: '비교 모드' }).click()

  menu = await openSkillMenu(page)
  calculation = menu.getByRole('menuitemcheckbox').filter({ hasText: '계산·단위 검증' })
  await expect(calculation).toBeDisabled()
  await expect(calculation).toContainText(
    '이 화면에서는 도구가 필요한 스킬을 실행할 수 없습니다.',
  )
})

test('선택한 스킬과 예상 토큰이 skills_applied 타임라인에 남는다', async ({ page }) => {
  await mockSkillWorkspace(page)
  await page.unroute(sessionsCollection)
  const fakeId = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
  const sessions: Record<string, unknown>[] = []
  const sent: Record<string, unknown>[] = []

  await page.route(/\/api\/sessions(?:\?.*)?$/, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: sessions })
      return
    }
    if (route.request().method() !== 'POST') return route.continue()
    const request = route.request().postDataJSON() as { kind: string; model?: string }
    const row = {
      id: fakeId,
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
    sent.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body:
        `data: ${JSON.stringify({
          type: 'skills_applied',
          skills: [
            {
              id: catalogue[2].id,
              name: '의사결정 메모',
              catalogKey: 'decision-memo',
              estimatedTokens: 123,
            },
          ],
          estimatedTokens: 123,
        })}\n\n` +
        `data: ${JSON.stringify({ type: 'delta', text: '모의 응답입니다.' })}\n\n` +
        `data: ${JSON.stringify({
          type: 'usage',
          inputTokens: 20,
          outputTokens: 4,
          credits: 0,
        })}\n\n`,
    })
  })

  await page.goto('/new/chat')
  await selectSkill(page, '의사결정 메모')
  await page.getByLabel('프롬프트 입력').fill('타임라인 확인')
  await page.getByLabel('프롬프트 입력').press('Enter')

  const timeline = page.getByRole('button').filter({ hasText: '스킬 1개 적용' })
  await expect(timeline).toBeVisible({ timeout: 15_000 })
  expect(sent.at(-1)?.activatedSkillIds).toEqual([catalogue[2].id])

  await timeline.click()
  await expect(page.getByText(/의사결정 메모 · 약 123 토큰/)).toBeVisible()
  await expect(page.getByText('모의 응답입니다.')).toBeVisible()
})

test('개인정보 결정 재시도가 선택 스킬을 보존하고 두 SSE를 모두 표시한다', async ({ page }) => {
  await mockSkillWorkspace(page)
  await page.unroute(sessionsCollection)
  const fakeId = 'abababababababababababababababab'
  const session = {
    id: fakeId,
    kind: 'chat',
    title: '',
    projectId: null,
    agentId: null,
    model: 'mock/tool-model',
    artifactId: null,
    pinned: false,
    createdAt: now,
    updatedAt: now,
    messages: null,
    preview: null,
    messageCount: 0,
  }
  const sent: Record<string, unknown>[] = []

  await page.route(sessionsCollection, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: [session] })
      return
    }
    await route.fulfill({ status: 201, json: session })
  })
  await page.route('**/api/sessions/*/messages', async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>
    sent.push(payload)
    if (payload.privacyAction !== 'mask_external') {
      await route.fulfill({
        status: 409,
        json: {
          code: 'privacy_decision_required',
          findings: [{ category: 'email', source: 'skills', count: 1 }],
          requestedModels: ['mock/tool-model'],
          safeModels: [],
          allowedActions: ['mask_external', 'edit', 'cancel'],
          decisionToken: 'skill-bound-decision',
          detectorVersion: 'privacy-detector-v1',
          policyVersion: 'external-data-guard-v1',
        },
      })
      return
    }
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body:
        `data: ${JSON.stringify({
          type: 'privacy_route',
          requestedModels: ['mock/tool-model'],
          effectiveModels: ['mock/tool-model'],
          action: 'mask_external',
          dataBoundary: 'external',
        })}\n\n` +
        `data: ${JSON.stringify({
          type: 'skills_applied',
          skills: [
            {
              id: catalogue[2].id,
              name: '의사결정 메모',
              catalogKey: 'decision-memo',
              estimatedTokens: 123,
            },
          ],
          estimatedTokens: 123,
        })}\n\n` +
        `data: ${JSON.stringify({ type: 'delta', text: '보호된 응답' })}\n\n` +
        `data: ${JSON.stringify({
          type: 'usage',
          inputTokens: 1,
          outputTokens: 1,
          credits: 0,
        })}\n\n` +
        `data: ${JSON.stringify({ type: 'done' })}\n\n`,
    })
  })

  await page.goto('/new/chat')
  await selectSkill(page, '의사결정 메모')
  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill('contact person@example.com')
  await composer.press('Enter')

  const modal = page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })
  await expect(modal).toBeVisible()
  await modal.getByRole('button', { name: '가린 뒤 기존 모델 사용' }).click()

  await expect(page.getByRole('button').filter({ hasText: '스킬 1개 적용' })).toBeVisible()
  expect(sent).toHaveLength(2)
  for (const payload of sent) {
    expect(payload.activatedSkillIds).toEqual([catalogue[2].id])
  }
  expect(sent[1]).toMatchObject({
    privacyAction: 'mask_external',
    privacyDecisionToken: 'skill-bound-decision',
  })
})

test('늦은 409는 새 초안에서 고른 스킬을 덮어쓰지 않는다', async ({ page }) => {
  await mockSkillWorkspace(page)
  await page.unroute(sessionsCollection)
  const fakeId = 'cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd'
  const session = {
    id: fakeId,
    kind: 'chat',
    title: '',
    projectId: null,
    agentId: null,
    model: 'mock/tool-model',
    artifactId: null,
    pinned: false,
    createdAt: now,
    updatedAt: now,
    messages: null,
    preview: null,
    messageCount: 0,
  }
  await page.route(sessionsCollection, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: [session] })
      return
    }
    await route.fulfill({ status: 201, json: session })
  })

  let requestStarted = false
  let releaseDecision: (() => void) | undefined
  const gate = new Promise<void>((resolve) => {
    releaseDecision = resolve
  })
  await page.route('**/api/sessions/*/messages', async (route) => {
    requestStarted = true
    await gate
    await route.fulfill({
      status: 409,
      json: {
        code: 'privacy_decision_required',
        findings: [{ category: 'email', source: 'current_input', count: 1 }],
        requestedModels: ['mock/tool-model'],
        safeModels: [],
        allowedActions: ['mask_external', 'edit', 'cancel'],
        decisionToken: 'late-skill-decision',
        detectorVersion: 'privacy-detector-v1',
        policyVersion: 'external-data-guard-v1',
      },
    })
  })

  await page.goto('/new/chat')
  await selectSkill(page, '초안 구조화')
  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill('old person@example.com')
  await composer.press('Enter')
  await expect.poll(() => requestStarted).toBe(true)

  await selectSkill(page, '의사결정 메모')
  releaseDecision?.()
  const modal = page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })
  await expect(modal).toBeVisible()
  await modal.getByRole('button', { name: '편집으로 돌아가기' }).click()

  await expect(page.getByRole('button', { name: '의사결정 메모 제거' })).toBeVisible()
  await expect(page.getByRole('button', { name: '초안 구조화 제거' })).toHaveCount(0)
})

test('422로 거절된 턴은 입력 상태를 복원하고 재시도해도 한 번만 남는다', async ({ page }) => {
  await mockSkillWorkspace(page)
  await page.unroute(sessionsCollection)
  const fakeId = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
  const session = {
    id: fakeId,
    kind: 'chat',
    title: '',
    projectId: null,
    agentId: null,
    model: 'mock/tool-model',
    artifactId: null,
    pinned: false,
    createdAt: now,
    updatedAt: now,
    messages: null,
    preview: null,
    messageCount: 0,
  }
  const sessions: Record<string, unknown>[] = []
  const sent: Record<string, unknown>[] = []
  let attempts = 0

  await page.route(sessionsCollection, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: sessions })
      return
    }
    if (route.request().method() !== 'POST') return route.continue()
    sessions.splice(0, sessions.length, session)
    await route.fulfill({ status: 201, json: session })
  })
  // SessionPage tries to hydrate a newly-created empty session. This test owns
  // the transcript through the list response, so fail that extra read locally
  // instead of letting it escape to a backend.
  await page.route(new RegExp(`/api/sessions/${fakeId}$`), (route) =>
    route.fulfill({ status: 404, json: { detail: 'not_found' } }),
  )

  const fileId = '88888888-8888-4888-8888-888888888888'
  const fileName = '검토자료.txt'
  await page.route(/\/api\/files$/, async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 201,
      json: {
        id: fileId,
        name: fileName,
        size: 24,
        mime: 'text/plain',
        tokens: 6,
        projectId: null,
        sessionId: null,
        sourceUrl: null,
        preview: '검토할 자료',
        error: null,
        indexed: true,
        createdAt: now,
      },
    })
  })
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    attempts += 1
    sent.push(route.request().postDataJSON() as Record<string, unknown>)
    if (attempts === 1) {
      await route.fulfill({
        status: 422,
        json: { detail: 'skill_not_available' },
      })
      return
    }
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body:
        `data: ${JSON.stringify({
          type: 'skills_applied',
          skills: [
            {
              id: catalogue[2].id,
              name: '의사결정 메모',
              catalogKey: 'decision-memo',
              estimatedTokens: 123,
            },
          ],
          estimatedTokens: 123,
        })}\n\n` +
        `data: ${JSON.stringify({ type: 'delta', text: '재시도 응답입니다.' })}\n\n` +
        `data: ${JSON.stringify({
          type: 'usage',
          inputTokens: 24,
          outputTokens: 5,
          credits: 0,
        })}\n\n`,
    })
  })

  const prompt = '첨부 자료를 의사결정 메모로 정리해줘'
  await page.goto('/new/chat')
  await selectSkill(page, '의사결정 메모')
  await Promise.all([
    page.waitForResponse(
      (response) => response.request().method() === 'POST' && response.url().endsWith('/api/files'),
    ),
    page.getByLabel('파일 선택').setInputFiles({
      name: fileName,
      mimeType: 'text/plain',
      buffer: Buffer.from('검토할 자료입니다.', 'utf-8'),
    }),
  ])
  await expect(page.getByRole('button', { name: `${fileName} 제거` })).toBeVisible()

  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill(prompt)
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.status() === 422 && response.url().endsWith(`/api/sessions/${fakeId}/messages`),
    ),
    composer.press('Enter'),
  ])

  // A 422 is pre-write: the exact draft is available to fix or retry, while
  // neither half of the optimistic turn remains in the transcript.
  await expect(composer).toHaveValue(prompt)
  await expect(page.getByRole('button', { name: '의사결정 메모 제거' })).toBeVisible()
  await expect(page.getByRole('button', { name: `${fileName} 제거` })).toBeVisible()
  await expect(page.getByRole('button', { name: '프롬프트 복사' })).toHaveCount(0)
  await expect(page.getByText('생각하는 중…')).toHaveCount(0)

  await composer.press('Enter')
  const timeline = page.getByRole('button').filter({ hasText: '스킬 1개 적용' })
  await expect(timeline).toHaveCount(1)
  await expect(page.getByText('재시도 응답입니다.')).toBeVisible()
  await expect(page.getByRole('button', { name: '프롬프트 복사' })).toHaveCount(1)
  await expect(page.getByRole('button', { name: '좋아요' })).toHaveCount(1)
  await expect(page.getByRole('button', { name: `${fileName} 제거` })).toHaveCount(0)
  await expect(page.getByText(fileName, { exact: true })).toHaveCount(1)

  expect(sent).toHaveLength(2)
  for (const payload of sent) {
    expect(payload).toMatchObject({
      content: prompt,
      activatedSkillIds: [catalogue[2].id],
      attachments: [fileId],
    })
  }
})

test('보고서와 슬라이드의 422도 로컬 초안 아티팩트를 되돌린다', async ({ page }) => {
  await mockSkillWorkspace(page)
  await page.unroute(sessionsCollection)
  const sessions: Record<string, unknown>[] = []
  let sequence = 0

  await page.route(sessionsCollection, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: sessions })
      return
    }
    if (route.request().method() !== 'POST') return route.continue()
    sequence += 1
    const request = route.request().postDataJSON() as { kind: string; model?: string }
    const row = {
      id: String(sequence).padStart(32, 'c'),
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
    sessions.unshift(row)
    await route.fulfill({ status: 201, json: row })
  })
  await page.route(/\/api\/sessions\/[^/?]+$/, (route) =>
    route.fulfill({ status: 404, json: { detail: 'not_found' } }),
  )
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({ status: 422, json: { detail: 'skill_not_available' } })
  })

  for (const surface of ['report', 'slides'] as const) {
    const prompt = `${surface} 거절 복구 확인`
    await page.goto(`/new/${surface}`)
    await selectSkill(page, '의사결정 메모')
    const composer = page.getByLabel('프롬프트 입력')
    await composer.fill(prompt)
    await Promise.all([
      page.waitForResponse(
        (response) => response.status() === 422 && response.url().endsWith('/messages'),
      ),
      composer.press('Enter'),
    ])

    await expect(composer).toHaveValue(prompt)
    await expect(page.getByRole('button', { name: '의사결정 메모 제거' })).toBeVisible()
    await expect(page.getByRole('button', { name: '프롬프트 복사' })).toHaveCount(0)
    await expect(page.locator('[data-panel="artifact"]')).toHaveCount(0)
  }
})

test('스킬 설명만 편집해도 기존 절차 본문을 PATCH에 보존한다', async ({ page }) => {
  const mocked = await mockSkillWorkspace(page)
  const target = catalogue.at(-1)!
  const changedDescription = '설명만 바꾼 값'

  await page.goto('/skills')
  await page.getByText(target.name, { exact: true }).first().click()
  await page.getByRole('dialog', { name: target.name }).getByRole('button', { name: '편집' }).click()

  const edit = page.getByRole('dialog', { name: '스킬 편집' })
  const procedure = edit.getByRole('textbox', {
    name: '절차 모델이 그대로 따를 단계입니다.',
    exact: true,
  })
  await expect(procedure).toHaveValue(target.body)
  await edit.getByRole('textbox', { name: '설명', exact: true }).fill(changedDescription)
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.request().method() === 'PATCH' && response.url().endsWith(`/api/skills/${target.id}`),
    ),
    edit.getByRole('button', { name: '저장', exact: true }).click(),
  ])

  expect(mocked.lastPatch()).toMatchObject({
    description: changedDescription,
    body: target.body,
  })

  await page.reload()
  await page.getByText(target.name, { exact: true }).first().click()
  await page.getByRole('dialog', { name: target.name }).getByRole('button', { name: '편집' }).click()
  await expect(
    page.getByRole('dialog', { name: '스킬 편집' }).getByRole('textbox', {
      name: '절차 모델이 그대로 따를 단계입니다.',
      exact: true,
    }),
  ).toHaveValue(target.body)
  expect(mocked.rows().find((row) => row.id === target.id)?.body).toBe(target.body)
})
