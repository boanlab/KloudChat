import type { Page } from '@playwright/test'
import type { RoutingMode, SuperAgentRoute, TurnReasoning } from '../src/types'

/** The Super Agent screens answered by fixtures: models, the picker's offer, one conversation,
 *  and a turn that answers with `route` (and `reasoning` on its line). */

export const now = '2026-11-13T00:00:00.000Z'
export const sessionId = 'cccccccccccccccccccccccccccccccc'

export const model = (id: string, label: string, boundary: 'self_hosted' | 'hybrid' | 'external', cost = 0, reasoningControl = false) => ({
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
  reasoningControl,
  description: `${label} description`,
})

export const models = [
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
  escalation: { model: 'external/deep', strictModel: 'strict-local/flash', effort: 'high' },
}

export type State = {
  sessions: Record<string, unknown>[]
  /** Turns sent, as posted. */
  turns: Record<string, unknown>[]
  creates: Record<string, unknown>[]
  patches: Record<string, unknown>[]
  governancePuts: Record<string, unknown>[]
  failGovernance: boolean
}

export async function mockApp(
  page: Page,
  initial: RoutingMode = 'manual',
  route?: SuperAgentRoute,
  modelByKind?: Record<string, string>,
  options: {
    reasoning?: TurnReasoning
    showUsage?: boolean
    catalogue?: ReturnType<typeof model>[]
    /** The catalogue's `defaultRoutingByKind` (설정 › 시스템 › 모델); absent: Super Agent. */
    defaultRouting?: Record<string, 'super' | 'super_strict' | 'manual'>
  } = {},
) {
  await page.route('**/api/**', (r) => r.abort('blockedbyclient'))
  const state: State = { sessions: [], turns: [], creates: [], patches: [], governancePuts: [], failGovernance: false }
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
          preferences: {
            streamResponses: true, autoMemory: false, showUsage: options.showUsage ?? false, privacyDefaultAction: 'ask',
            ...(modelByKind ? { modelByKind } : {}),
          },
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
        models: options.catalogue ?? models,
        litellmAvailable: true,
        defaultChatModel: 'local/flash',
        autoRouting: {
          enabled: true, available: true, reason: null, classifierModelId: 'strict-local/flash',
          economyModelIds: ['local/flash'], qualityAvailable: true, qualityReason: null,
          qualityModelIds: ['external/deep'],
        },
        ...(options.defaultRouting ? { defaultRoutingByKind: options.defaultRouting } : {}),
        superAgent: {
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
      reasoningMode: payload.reasoningMode ?? null,
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
    state.turns.push(r.request().postDataJSON() as Record<string, unknown>)
    const routing = {
      requestedModels: [route?.selectedModel ?? 'local/flash'],
      routedModels: [route?.selectedModel ?? 'local/flash'],
      effectiveModels: [route?.selectedModel ?? 'local/flash'],
      actualModels: [route?.selectedModel ?? 'local/flash'],
      action: 'none',
      dataBoundary: 'hybrid',
      ...(route ? { superAgent: route } : {}),
      ...(options.reasoning ? { reasoning: options.reasoning } : {}),
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

