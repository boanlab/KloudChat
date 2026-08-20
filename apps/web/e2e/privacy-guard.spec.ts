import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

const DECISION = {
  code: 'privacy_decision_required',
  findings: [{ category: 'email', source: 'current_input', count: 1 }],
  requestedModels: ['external/model'],
  safeModels: [],
  allowedActions: ['mask_external', 'edit', 'cancel'],
  decisionToken: 'test-decision-token',
  detectorVersion: 'privacy-detector-v1',
  policyVersion: 'external-data-guard-v1',
}

const chatModel = (id: string, label: string) => ({
  id,
  label,
  name: label,
  vendor: 'Test',
  provider: 'openrouter',
  dataBoundary: 'external',
  strictLocal: false,
  privacyOnly: false,
  modality: 'chat',
  kinds: ['chat'],
  creditCost: 1,
  inputCreditCost: 1,
  supportsTools: false,
  description: '',
})

const emptyChatSession = (id: string, model = 'external/one') => ({
  id,
  kind: 'chat',
  title: '새 작업',
  projectId: null,
  agentId: null,
  model,
  artifactId: null,
  pinned: false,
  createdAt: '2026-08-16T00:00:00Z',
  updatedAt: '2026-08-16T00:00:00Z',
  messages: [],
  preview: null,
  messageCount: 0,
})

test('privacy decision preserves a draft and retries with the bound action', async ({ page }) => {
  await signIn(page)

  const requests: Record<string, unknown>[] = []
  const messageUrls: string[] = []
  await page.route('**/api/files', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'privacy-file-1',
        name: 'contacts.txt',
        size: 24,
        mime: 'text/plain',
        tokens: 6,
        projectId: null,
        sessionId: null,
        preview: 'person@example.com',
        error: null,
        createdAt: new Date().toISOString(),
      }),
    })
  })
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    const payload = route.request().postDataJSON() as Record<string, unknown>
    requests.push(payload)
    messageUrls.push(route.request().url())
    if (payload.privacyAction !== 'mask_external') {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify(DECISION),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: [
        'data: {"type":"privacy_route","requestedModels":["external/model"],"effectiveModels":["external/model"],"action":"mask_external","dataBoundary":"external"}',
        'data: {"type":"delta","text":"protected answer"}',
        'data: {"type":"usage","inputTokens":1,"outputTokens":2,"credits":0}',
        'data: {"type":"done"}',
        '',
      ].join('\n'),
    })
  })

  await page.goto('/new/chat')
  const composer = page.getByLabel('프롬프트 입력')
  await page.getByLabel('파일 선택').setInputFiles({
    name: 'contacts.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('person@example.com'),
  })
  await expect(page.getByText('contacts.txt')).toBeVisible()
  const original = 'contact person@example.com'
  await composer.fill(original)
  await composer.press('Enter')

  const modal = page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })
  await expect(modal).toBeVisible()
  await expect(modal.getByText('현재 요청 · 이메일 1')).toBeVisible()

  const close = modal.getByRole('button', { name: '닫기' })
  await expect(close).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(modal.getByRole('button', { name: '편집으로 돌아가기' })).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(modal).toBeHidden()
  await expect(composer).toHaveValue(original)
  await expect(composer).toBeFocused()
  await expect(page.getByText('contacts.txt')).toBeVisible()

  await composer.press('Enter')
  await expect(modal).toBeVisible()
  expect(messageUrls[1]).toBe(messageUrls[0])
  await modal.getByRole('button', { name: '가린 뒤 기존 모델 사용' }).click()

  await expect(page).toHaveURL(/\/s\//)
  await expect
    .poll(() => requests.at(-1))
    .toMatchObject({
      content: original,
      attachments: ['privacy-file-1'],
      privacyAction: 'mask_external',
      privacyDecisionToken: DECISION.decisionToken,
  })
})

test('model changes after a decision update the hidden reusable session', async ({ page }) => {
  await page.route('**/api/models', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        models: [
          chatModel('external/one', 'External One'),
          chatModel('external/two', 'External Two'),
        ],
        litellmAvailable: true,
        defaultChatModel: 'external/one',
      }),
    })
  })
  await signIn(page)

  const hiddenSessionId = 'hidden-privacy-session'
  const session = emptyChatSession(hiddenSessionId)
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(session),
    })
  })
  const patches: Record<string, unknown>[] = []
  await page.route(`**/api/sessions/${hiddenSessionId}`, async (route) => {
    if (route.request().method() !== 'PATCH') {
      await route.continue()
      return
    }
    const payload = route.request().postDataJSON() as Record<string, unknown>
    patches.push(payload)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...session, model: payload.model }),
    })
  })
  await page.route(`**/api/sessions/${hiddenSessionId}/messages`, async (route) => {
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ ...DECISION, requestedModels: ['external/one'] }),
    })
  })

  await page.goto('/new/chat')
  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill('contact person@example.com')
  await composer.press('Enter')
  const modal = page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })
  await expect(modal).toBeVisible()
  await modal.getByRole('button', { name: '편집으로 돌아가기' }).click()
  await expect(page).toHaveURL(/\/new\/chat/)

  await page.getByRole('button', { name: /External One/ }).click()
  await page.getByRole('button', { name: /External Two/ }).click()

  await expect.poll(() => patches.length).toBe(1)
  expect(patches[0]).toEqual({ model: 'external/two', routingMode: 'manual' })
})

test('a late privacy decision never overwrites a newer draft or attachment', async ({ page }) => {
  await signIn(page)

  const hiddenSessionId = 'late-privacy-session'
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(emptyChatSession(hiddenSessionId)),
    })
  })
  const uploads = [
    {
      id: 'old-file',
      name: 'old.txt',
      preview: 'old@example.com',
    },
    {
      id: 'new-file',
      name: 'new.txt',
      preview: 'new safe draft',
    },
  ]
  let uploadIndex = 0
  await page.route('**/api/files', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    const upload = uploads[uploadIndex++]
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        ...upload,
        size: 16,
        mime: 'text/plain',
        tokens: 4,
        projectId: null,
        sessionId: null,
        error: null,
        createdAt: '2026-08-16T00:00:00Z',
      }),
    })
  })

  let requestStarted = false
  let releaseDecision: (() => void) | undefined
  const decisionGate = new Promise<void>((resolve) => {
    releaseDecision = resolve
  })
  await page.route(`**/api/sessions/${hiddenSessionId}/messages`, async (route) => {
    requestStarted = true
    await decisionGate
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify(DECISION),
    })
  })

  await page.goto('/new/chat')
  const composer = page.getByLabel('프롬프트 입력')
  await page.getByLabel('파일 선택').setInputFiles({
    name: 'old.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('old@example.com'),
  })
  await composer.fill('old request for old@example.com')
  await composer.press('Enter')
  await expect.poll(() => requestStarted).toBe(true)
  await expect(composer).toHaveValue('')

  await page.getByLabel('파일 선택').setInputFiles({
    name: 'new.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('new safe draft'),
  })
  const newerDraft = 'this newer draft must win'
  await composer.fill(newerDraft)
  releaseDecision?.()

  const modal = page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })
  await expect(modal).toBeVisible()
  await modal.getByRole('button', { name: '편집으로 돌아가기' }).click()
  await expect(composer).toHaveValue(newerDraft)
  await expect(page.getByRole('button', { name: 'new.txt 제거' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'old.txt 제거' })).toHaveCount(0)
})

test('governance outage restores the cleared draft and reuses its empty session', async ({
  page,
}) => {
  await signIn(page)

  await page.route('**/api/files', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'outage-file-1',
        name: 'outage-notes.txt',
        size: 16,
        mime: 'text/plain',
        tokens: 4,
        projectId: null,
        sessionId: null,
        preview: 'safe notes',
        error: null,
        createdAt: new Date().toISOString(),
      }),
    })
  })
  const messageUrls: string[] = []
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    messageUrls.push(route.request().url())
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'governance_unavailable' }),
    })
  })

  await page.goto('/new/chat')
  await page.getByLabel('파일 선택').setInputFiles({
    name: 'outage-notes.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('safe notes'),
  })
  const composer = page.getByLabel('프롬프트 입력')
  const original = 'keep this draft during an outage'
  await composer.fill(original)
  await composer.press('Enter')

  await expect(page).toHaveURL(/\/s\//)
  await expect(composer).toHaveValue(original)
  await expect(page.getByRole('button', { name: 'outage-notes.txt 제거' })).toBeVisible()
  await expect(page.getByRole('alert')).toBeVisible()

  await composer.press('Enter')
  await expect.poll(() => messageUrls.length).toBe(2)
  expect(messageUrls[1]).toBe(messageUrls[0])
  await expect(composer).toHaveValue(original)
  await expect(page.getByRole('button', { name: 'outage-notes.txt 제거' })).toBeVisible()
})

test('comparison retry keeps attachment ids and starts no column before a decision', async ({
  page,
}) => {
  await page.route('**/api/models', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        models: [
          chatModel('external/one', 'external/one'),
          chatModel('external/two', 'external/two'),
        ],
        litellmAvailable: true,
        defaultChatModel: 'external/one',
      }),
    })
  })
  await signIn(page)

  await page.route('**/api/files', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'compare-private-file',
        name: 'private.txt',
        size: 20,
        mime: 'text/plain',
        tokens: 5,
        projectId: null,
        sessionId: null,
        preview: 'person@example.com',
        error: null,
        createdAt: new Date().toISOString(),
      }),
    })
  })
  const requests: Record<string, unknown>[] = []
  await page.route('**/api/sessions/*/compare', async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>
    requests.push(payload)
    if (payload.privacyAction !== 'mask_external') {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          ...DECISION,
          requestedModels: ['external/one', 'external/two'],
          findings: [{ category: 'email', source: 'attachments', count: 1 }],
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: [
        'data: {"type":"privacy_route","requestedModels":["external/one","external/two"],"routedModels":["external/one","external/two"],"effectiveModels":["external/one","external/two"],"actualModels":[],"action":"mask_external","dataBoundary":"external"}',
        'data: {"type":"variant_done","model":"external/one","credits":0,"inputTokens":0,"outputTokens":0,"error":null}',
        'data: {"type":"variant_done","model":"external/two","credits":0,"inputTokens":0,"outputTokens":0,"error":null}',
        'data: {"type":"done","credits":0}',
        '',
      ].join('\n'),
    })
  })

  await page.goto('/new/chat')
  await page.getByRole('button', { name: '모델 비교' }).click()
  await page.getByRole('menuitem', { name: /비교 모드/ }).click()
  await page.getByLabel('파일 선택').setInputFiles({
    name: 'private.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('person@example.com'),
  })
  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill('clean comparison request')
  await composer.press('Enter')

  const modal = page.getByRole('dialog', { name: '개인정보가 포함된 요청입니다' })
  await expect(modal).toBeVisible()
  expect(requests).toHaveLength(1)
  await modal.getByRole('button', { name: '가린 뒤 기존 모델 사용' }).click()

  await expect.poll(() => requests.length).toBe(2)
  expect(requests[1]).toMatchObject({
    attachments: ['compare-private-file'],
    models: ['external/one', 'external/two'],
    privacyAction: 'mask_external',
  })
})

/**
 * The record and the screen disagree the moment the detector finds something:
 * the Message is stored masked whatever action was taken, while the bubble
 * still holds what was typed until the session is next opened. The turn is
 * where that has to be said — a week later there is only a sentence with holes
 * in it and nobody to ask.
 */
test('가려진 채 저장된다는 사실을 그 턴 안에서 말한다', async ({ page }) => {
  await signIn(page)

  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: [
        // `send_raw_external`: the envelope goes out untouched and the stored
        // message is masked all the same.
        'data: {"type":"privacy_route","requestedModels":["external/model"],"routedModels":["external/model"],"effectiveModels":["external/model"],"actualModels":[],"action":"send_raw_external","dataBoundary":"external","findingCounts":[{"category":"email","source":"current_input","count":1}]}',
        'data: {"type":"delta","text":"원문 그대로 보냈습니다."}',
        'data: {"type":"usage","inputTokens":1,"outputTokens":2,"credits":0}',
        'data: {"type":"done"}',
        '',
      ].join('\n'),
    })
  })

  await page.goto('/new/chat')
  const composer = page.getByLabel('프롬프트 입력')
  const typed = '연락처는 person@example.com 입니다'
  await composer.fill(typed)
  await composer.press('Enter')

  await expect(page.getByText('이메일 1')).toBeVisible()
  await expect(
    page.getByText('기록에는 가려진 채 저장됩니다. 이 대화를 다시 열면 여기에도 자리표시자만 남습니다.'),
  ).toBeVisible()
  // The bubble is not rewritten: what was sent this time really was the
  // original, and pretending otherwise is its own lie.
  await expect(page.getByText(typed)).toBeVisible()
})
