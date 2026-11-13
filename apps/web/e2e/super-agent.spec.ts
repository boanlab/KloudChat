import { expect, test, type Page } from '@playwright/test'
import type { RoutingMode, SuperAgentRoute } from '../src/types'
import { setWebSearch } from './composer-tools'

const now = '2026-11-13T00:00:00.000Z'
const sessionId = 'cccccccccccccccccccccccccccccccc'

const model = (id: string, label: string, boundary: 'self_hosted' | 'hybrid' | 'external', cost = 0) => ({
  id,
  label,
  name: label,
  vendor: 'Mock',
  provider: boundary === 'external' ? 'openrouter' : 'hosted_vllm',
  dataBoundary: boundary,
  strictLocal: boundary === 'self_hosted',
  privacyOnly: boundary === 'self_hosted',
  modality: 'chat',
  kinds: ['chat', 'report', 'slides'],
  creditCost: cost,
  inputCreditCost: cost,
  contextWindow: 128_000,
  supportsVision: false,
  supportsTools: true,
  description: `${label} description`,
})

const models = [
  model('local/flash', 'Mock · Flash', 'hybrid'),
  model('strict-local/flash', 'Mock · Strict Flash', 'self_hosted'),
  model('external/deep', 'Mock · Deep', 'external', 40),
]

const defaultRules = {
  simple: { model: 'local/flash', strictModel: 'strict-local/flash', effort: 'none' },
  chat: { model: 'local/flash', strictModel: 'strict-local/flash', effort: 'none' },
  transform: { model: 'local/flash', strictModel: 'strict-local/flash', effort: 'none' },
  calc: { model: 'external/deep', strictModel: 'strict-local/flash', effort: 'high' },
  code: { model: 'external/deep', strictModel: 'strict-local/flash', effort: 'high' },
  current: { model: 'external/deep', strictModel: 'strict-local/flash', effort: 'none' },
  long: { model: 'external/deep', strictModel: 'strict-local/flash', effort: 'high' },
  reasoning: { model: 'external/deep', strictModel: 'strict-local/flash', effort: 'high' },
  document: { model: 'local/flash', strictModel: 'strict-local/flash', effort: 'default' },
}

type State = {
  sessions: Record<string, unknown>[]
  creates: Record<string, unknown>[]
  patches: Record<string, unknown>[]
  governancePuts: Record<string, unknown>[]
  failGovernance: boolean
}

async function mockApp(page: Page, initial: RoutingMode = 'manual', route?: SuperAgentRoute) {
  await page.route('**/api/**', (r) => r.abort('blockedbyclient'))
  const state: State = { sessions: [], creates: [], patches: [], governancePuts: [], failGovernance: false }
  let governance: Record<string, unknown> = {
    piiMasking: false,
    externalDataGuard: false,
    allowUserRawExternal: false,
    privacySafeModelIds: [],
    intentFilter: false,
    blockedCategories: [],
    retentionDays: 0,
    idleTimeoutMinutes: 0,
    adaptiveRoutingEnabled: false,
    adaptiveClassifierModelId: 'strict-local/flash',
    adaptiveEconomyModelIds: [],
    adaptiveQualityEnabled: false,
    adaptiveQualityModelIds: [],
    superAgentEnabled: true,
    superAgentRules: defaultRules,
    superAgentDefaultRules: defaultRules,
    internalDataStrictLocal: true,
    outlineModelId: null,
  }
  await page.route(/\/api\/auth\/refresh$/, (r) =>
    r.fulfill({
      json: {
        accessToken: 't',
        expiresIn: 3_600,
        user: {
          id: 'user-1', email: 's@example.com', name: 'Super', role: 'admin', status: 'active',
          monthlyCredits: 10_000, creditsUsed: 0, cycleResetsAt: null, avatarColor: '#64748b',
          litellmKeyPreview: null, litellmKeyIssuedAt: null,
          preferences: { streamResponses: true, autoMemory: false, showUsage: false, privacyDefaultAction: 'ask' },
          allowedModels: [], createdAt: now, lastActiveAt: now,
        },
      },
    }),
  )
  await page.route(/\/api\/auth\/config$/, (r) =>
    r.fulfill({
      json: {
        passwordResetEnabled: false, dictationEnabled: false, brand: { name: 'KloudChat', logo: '' },
        enabledKinds: ['chat', 'report', 'slides'], privacy: { externalDataGuard: false, allowUserRawExternal: false },
      },
    }),
  )
  await page.route(/\/api\/models$/, (r) =>
    r.fulfill({
      json: {
        models,
        litellmAvailable: true,
        defaultChatModel: 'local/flash',
        autoRouting: {
          enabled: true, available: true, reason: null, classifierModelId: 'strict-local/flash',
          economyModelIds: ['local/flash'], qualityAvailable: true, qualityReason: null,
          qualityModelIds: ['external/deep'],
        },
        superAgent: {
          enabled: true,
          default: { available: true, reason: null, estimate: { min: 0, max: 440, models: [] } },
          strict: { available: true, reason: null, estimate: { min: 0, max: 0, models: [] } },
        },
      },
    }),
  )
  for (const endpoint of [
    /\/api\/projects(?:\?.*)?$/, /\/api\/artifacts(?:\?.*)?$/, /\/api\/skills(?:\?.*)?$/,
    /\/api\/memory(?:\?.*)?$/, /\/api\/agents(?:\?.*)?$/, /\/api\/tools(?:\?.*)?$/,
    /\/api\/templates(?:\?.*)?$/, /\/api\/connectors(?:\?.*)?$/, /\/api\/connectors\/catalog(?:\?.*)?$/,
    /\/api\/files(?:\?.*)?$/,
  ]) {
    await page.route(endpoint, (r) => r.fulfill({ json: [] }))
  }
  await page.route(/\/api\/admin\/governance$/, async (r) => {
    if (r.request().method() === 'GET') {
      await r.fulfill({ json: governance })
      return
    }
    const payload = r.request().postDataJSON() as Record<string, unknown>
    state.governancePuts.push(payload)
    if (state.failGovernance) {
      await r.fulfill({ status: 422, json: { detail: 'super_agent_strict_model_must_be_strict_local' } })
      return
    }
    governance = { ...governance, ...payload }
    await r.fulfill({ json: { ok: true, clearedMessages: 0 } })
  })
  await page.route(/\/api\/admin\/settings$/, (r) =>
    r.fulfill({
      json: {
        litellm: { baseUrl: 'http://litellm:4000', baseUrlSource: 'database', masterKeySet: true, masterKeyPreview: '1234', masterKeySource: 'database' },
        smtp: { host: '', port: '', security: 'starttls', username: '', from: '', appBaseUrl: '', passwordSet: false, passwordPreview: '', hostSource: 'environment', passwordResetEnabled: false },
        status: 'ok', brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
        tools: { backendBaseUrl: '', features: [] }, credits: { perUsd: 1_000, budgetHeadroom: 1.1 }, unpricedModels: [],
      },
    }),
  )
  const row = (mode: RoutingMode) => ({
    id: sessionId, kind: 'chat', title: 'Super 대화', projectId: null, agentId: null, model: '',
    routingMode: mode, artifactId: null, pinned: false, createdAt: now, updatedAt: now,
    messages: [], preview: null, messageCount: 0,
  })
  await page.route(/\/api\/sessions(?:\?.*)?$/, async (r) => {
    if (r.request().method() === 'GET') {
      await r.fulfill({ json: state.sessions.map((s) => ({ ...s, messages: null })) })
      return
    }
    const payload = r.request().postDataJSON() as Record<string, unknown>
    state.creates.push(payload)
    const created = {
      ...row((payload.routingMode as RoutingMode) ?? 'manual'),
      id: (payload.id as string | undefined) ?? sessionId,
      model: payload.model ?? '',
    }
    state.sessions = [created]
    await r.fulfill({ status: 201, json: created })
  })
  await page.route(/\/api\/sessions\/[a-z0-9]+$/, async (r) => {
    const current = state.sessions[0]
    if (r.request().method() === 'GET') {
      await r.fulfill({ json: current })
      return
    }
    const payload = r.request().postDataJSON() as Record<string, unknown>
    state.patches.push(payload)
    state.sessions = [{ ...current, ...payload }]
    await r.fulfill({ json: state.sessions[0] })
  })
  await page.route('**/api/sessions/*/messages', async (r) => {
    const routing = {
      requestedModels: [route?.selectedModel ?? 'local/flash'],
      routedModels: [route?.selectedModel ?? 'local/flash'],
      effectiveModels: [route?.selectedModel ?? 'local/flash'],
      actualModels: [route?.selectedModel ?? 'local/flash'],
      action: 'none',
      dataBoundary: 'hybrid',
      ...(route ? { superAgent: route } : {}),
    }
    state.sessions = state.sessions.map((s) => ({
      ...s,
      messages: [
        { id: 'u1', role: 'user', content: '안녕', attachments: [], steps: null, variants: null, usage: null, model: null, routing: null, createdAt: now },
        { id: 'a1', role: 'assistant', content: '안녕하세요!', attachments: [], steps: null, variants: null, usage: { inputTokens: 3, outputTokens: 1, credits: 0 }, model: routing.actualModels[0], routing, createdAt: now },
      ],
      messageCount: 2,
    }))
    await r.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: [
        `data: ${JSON.stringify({ type: 'privacy_route', ...routing })}`,
        'data: {"type":"delta","text":"안녕하세요!"}',
        'data: {"type":"usage","inputTokens":3,"outputTokens":1,"credits":0}',
        'data: {"type":"done"}',
        '',
      ].join('\n'),
    })
  })
  if (initial !== 'manual') state.sessions = [row(initial)]
  return state
}

test('모델 선택기는 Auto 두 항목 대신 Super Agent 두 항목을 보여 주고 고르면 대화에 저장한다', async ({ page }) => {
  const state = await mockApp(page, 'manual')
  state.sessions = [{ ...state.sessions[0], id: sessionId, kind: 'chat', title: '', projectId: null, agentId: null, model: 'local/flash', routingMode: 'manual', artifactId: null, pinned: false, createdAt: now, updatedAt: now, messages: [], preview: null, messageCount: 0 }]
  await page.goto(`/s/${sessionId}`)
  await page.getByRole('button', { name: /Mock · Flash/ }).click()
  const superEntry = page.getByRole('menuitemradio', { name: /^Super Agent 사용 가능/ })
  const strictEntry = page.getByRole('menuitemradio', { name: /Super Agent \(strict-local\)/ })
  await expect(superEntry).toContainText('질문마다 알맞은 모델과 추론 강도를 골라')
  await expect(superEntry).toContainText('답 하나에 보통 0~440 크레딧')
  await expect(strictEntry).toContainText('질문과 자료가 밖으로 나가지 않습니다')
  await expect(strictEntry).toContainText('무료(내부 모델)')
  await expect(page.getByRole('menuitemradio', { name: /Auto · 비용 절약|Auto · 품질 우선/ })).toHaveCount(0)
  await strictEntry.click()
  await expect.poll(() => state.patches.at(-1)).toMatchObject({ routingMode: 'super_strict' })
  await expect(page.getByRole('button', { name: 'Super Agent (strict-local)' })).toBeVisible()
})

test('새 대화에서 Super Agent를 고르면 그 모드로 한 번만 만든다', async ({ page }) => {
  const state = await mockApp(page)
  await page.goto('/new/chat')
  await page.getByRole('button', { name: /Mock · Flash/ }).click()
  await page.getByRole('menuitemradio', { name: /^Super Agent 사용 가능/ }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}$/)
  await expect(page.getByRole('button', { name: 'Super Agent', exact: true })).toBeVisible()
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('안녕')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => state.creates.length).toBe(1)
  expect(state.creates[0]).toMatchObject({ kind: 'chat', routingMode: 'super' })
})

test('답 아래에 이번 답의 모델과 이유를 보여 준다 @phone', async ({ page }) => {
  await mockApp(page, 'super', {
    mode: 'super', taskClass: 'simple', source: 'rule', reasonCode: 'greeting',
    selectedModel: 'local/flash', effort: 'none', pick: 'rule', tools: 'none', routerVersion: 'v',
  })
  await page.goto(`/s/${sessionId}`)
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('안녕')
  await page.getByLabel('프롬프트 입력').press('Enter')
  if ((page.viewportSize()?.width ?? 1440) < 640) {
    await page.getByRole('button', { name: /처리 내역 \d+건/ }).click()
  }
  const badge = page.getByText('이번 답: Mock · Flash · 간단한 질문')
  await expect(badge).toBeVisible()
  await expect(badge).toHaveAttribute('title', /규칙.*추론 끔.*도구 없이/)
  await page.reload()
  if ((page.viewportSize()?.width ?? 1440) < 640) {
    await page.getByRole('button', { name: /처리 내역 \d+건/ }).click()
  }
  await expect(badge).toBeVisible()
})

test('개인정보로 내부 모델로 옮긴 답은 그 이유를 말한다', async ({ page }) => {
  await mockApp(page, 'super', {
    mode: 'super', taskClass: 'reasoning', source: 'rule', reasonCode: 'reasoning_markers',
    selectedModel: 'strict-local/flash', effort: 'high', pick: 'rule', tools: 'full', routerVersion: 'v',
    strictReason: 'personal_data',
  })
  await page.goto(`/s/${sessionId}`)
  await setWebSearch(page, '끔')
  await page.getByLabel('프롬프트 입력').fill('고객 010-1234-5678 사과문')
  await page.getByLabel('프롬프트 입력').press('Enter')
  if ((page.viewportSize()?.width ?? 1440) < 640) {
    await page.getByRole('button', { name: /처리 내역 \d+건/ }).click()
  }
  await expect(page.getByText('이번 답: Mock · Strict Flash · 복잡한 추론 · 개인정보 감지로 내부 모델')).toBeVisible()
})

test('관리자는 작업 종류별 표를 고치고, 거부는 성공으로 보이지 않는다', async ({ page }) => {
  const state = await mockApp(page)
  await page.goto('/admin/system/routing')
  await page.getByLabel('코드 · Super Agent 모델').selectOption('local/flash')
  await page.getByLabel('코드 · 추론 강도').selectOption('low')
  state.failGovernance = true
  await page.getByRole('button', { name: 'Super Agent 설정 저장' }).click()
  await expect(page.getByRole('alert')).toContainText('strict-local 열에는 strict-local 모델만')
  await expect(page.getByText('저장했습니다.')).toHaveCount(0)
  state.failGovernance = false
  await page.getByRole('button', { name: 'Super Agent 설정 저장' }).click()
  await expect(page.getByText('저장했습니다.')).toBeVisible()
  expect(state.governancePuts.at(-1)).toMatchObject({
    superAgentEnabled: true,
    superAgentRules: { code: { model: 'local/flash', effort: 'low' } },
  })
  await page.getByRole('button', { name: '표를 기본값으로' }).click()
  await expect(page.getByLabel('코드 · Super Agent 모델')).toHaveValue('external/deep')
})
