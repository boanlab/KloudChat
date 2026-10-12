import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'
import { openTool, setWebSearch, toolNames } from './composer-tools'

/** Composer toolbar switches say what the turn will do and which of them are account-wide. */

const chatModel = (id: string, label: string, strictLocal = false) => ({
  id,
  label,
  name: label,
  vendor: 'Test',
  provider: strictLocal ? 'ollama' : 'openrouter',
  dataBoundary: strictLocal ? 'self_hosted' : 'external',
  strictLocal,
  privacyOnly: false,
  modality: 'chat',
  kinds: ['chat'],
  creditCost: strictLocal ? 0 : 1,
  inputCreditCost: strictLocal ? 0 : 1,
  supportsTools: false,
  description: '',
})

const emptySession = {
  id: 'per-turn-session',
  kind: 'chat',
  title: '새 작업',
  projectId: null,
  agentId: null,
  model: 'external/one',
  artifactId: null,
  pinned: false,
  createdAt: '2026-08-16T00:00:00Z',
  updatedAt: '2026-08-16T00:00:00Z',
  messages: [],
  preview: null,
  messageCount: 0,
}

async function stubModels(page: Page) {
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
          chatModel('local/strict', 'Local Strict', true),
        ],
        litellmAvailable: true,
        defaultChatModel: 'external/one',
      }),
    })
  })
}

test('웹 검색은 strict-local 모델에서 켜진 척하지 않는다', async ({ page }) => {
  await stubModels(page)
  await signIn(page)

  const payloads: Record<string, unknown>[] = []
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(emptySession),
    })
  })
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    payloads.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: [
        'data: {"type":"delta","text":"검색 없이 답합니다"}',
        'data: {"type":"usage","inputTokens":1,"outputTokens":2,"credits":0}',
        'data: {"type":"done"}',
        '',
      ].join('\n'),
    })
  })

  await page.goto('/new/chat')
  await setWebSearch(page, '항상')
  await expect(page.getByText('웹 검색: 항상', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: /External One/ }).click()
  await page.getByRole('button', { name: /Local Strict/ }).click()

  // The wish stays on file; the row says what the turn will do, and the choices are off.
  await expect(page.getByText('웹 검색 안 함 · 이 모델은 외부에 연결하지 않습니다')).toBeVisible()
  await openTool(page, /^웹 검색/)
  await expect(page.getByRole('menuitemradio', { name: /^항상/ })).toBeDisabled()
  await page.keyboard.press('Escape')

  const composer = page.getByLabel('프롬프트 입력')
  await composer.fill('오늘 환율')
  await composer.press('Enter')
  await expect.poll(() => payloads.length).toBe(1)
  expect(payloads[0]).toMatchObject({ webSearch: false })
})

test('작성 도구 옆의 커넥터 스위치는 계정 전체 설정이라고 밝힌다', async ({ page }) => {
  await stubModels(page)
  await page.route('**/api/connectors', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: 'connector-1',
          name: 'Test Drive',
          slug: 'test-drive',
          description: '',
          category: 'files',
          transport: 'http',
          endpoint: 'https://mcp.test',
          auth: 'none',
          kinds: ['chat'],
          official: true,
          installed: true,
          enabled: true,
          status: 'connected',
          tools: [],
          lastSyncAt: null,
          error: null,
        },
      ]),
    })
  })
  await signIn(page)

  await page.goto('/new/chat')
  await openTool(page, /^커넥터/)
  await expect(
    page.getByText('계정 전체 설정입니다. 여기서 끄면 모든 대화에서 꺼집니다.'),
  ).toBeVisible()
})

test('웹 검색 토글은 글을 쓰는 모든 화면에 있다', async ({ page }) => {
  // One model every text surface may select.
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
          { ...chatModel('external/one', 'External One'), kinds: ['chat', 'report', 'slides'] },
        ],
        litellmAvailable: true,
        defaultChatModel: 'external/one',
      }),
    })
  })
  await signIn(page)

  const offersSearch = async () => (await toolNames(page)).filter((name) => name.startsWith('웹 검색')).length
  await page.goto('/new/chat')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  expect(await offersSearch()).toBe(1)

  // Document surfaces can search too (`canWebSearch` in `Composer`).
  for (const kind of ['report', 'slides']) {
    await page.goto(`/new/${kind}`)
    await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
    expect(await offersSearch()).toBe(1)
  }

  // Media surfaces hide it; asserted in `disabled-surface.spec.ts`, since they are off by default.
})
