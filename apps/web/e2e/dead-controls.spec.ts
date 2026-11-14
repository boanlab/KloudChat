import { expect, test, type Page } from '@playwright/test'
import { composerFixture } from './composer-fixture'

/** Rating and 원본 작업 열기 do what they look like they do. Stubbed: each is a claim about a
 *  request that was or was not made. (Web search per conversation: composer-handoff.spec.ts;
 *  a store agent's install: customize-forms.spec.ts.) */

const NOW = '2026-08-20T00:00:00Z'

const session = (changes: Record<string, unknown> = {}) => ({
  id: 'session-1',
  kind: 'chat',
  title: '보안 점검 보고서',
  projectId: null,
  agentId: null,
  model: 'external/one',
  routingMode: 'manual',
  artifactId: 'artifact-latest',
  renderTemplateId: null,
  pinned: false,
  createdAt: NOW,
  updatedAt: NOW,
  messages: null,
  preview: '네, 정리했습니다',
  messageCount: 2,
  ...changes,
})

const artifact = (id: string, title: string) => ({
  id,
  kind: 'html',
  title,
  version: 1,
  data: { html: `<!doctype html><title>${title}</title>` },
  sessionId: 'session-1',
  projectId: null,
  createdAt: NOW,
  updatedAt: NOW,
})

const answer = (rating: 'up' | 'down' | null) => ({
  id: 'message-2',
  role: 'assistant',
  content: '정리했습니다',
  steps: null,
  attachments: null,
  variants: null,
  usage: null,
  model: 'external/one',
  routing: null,
  startedFrom: null,
  rating,
  createdAt: NOW,
})

/** Answers one GET with fixed JSON, leaving every other method alone. */
async function stubGet(page: Page, pattern: string | RegExp, payload: unknown) {
  await page.route(pattern, async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    })
  })
}

// ── 1. 좋아요 / 싫어요 ──────────────────────────────────────────────────

test('싫어요는 서버에 남고, 다시 열었을 때 눌린 채로 보인다', async ({ page }, testInfo) => {
  await composerFixture(page, testInfo)

  let stored: 'up' | 'down' | null = null
  await stubGet(page, /\/api\/sessions(\?|$)/, [session()])
  await page.route('**/api/sessions/session-1', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        session({
          messages: [
            {
              ...answer(null),
              id: 'message-1',
              role: 'user',
              content: '점검 결과를 정리해 주세요',
            },
            answer(stored),
          ],
        }),
      ),
    })
  })
  // Stores and answers with the message, so the client holds the server's version.
  await page.route('**/api/messages/message-2/rating', async (route) => {
    stored = (route.request().postDataJSON() as { rating: 'up' | 'down' | null }).rating
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(answer(stored)),
    })
  })

  await page.goto('/s/session-1')
  const down = page.getByRole('button', { name: '싫어요' })
  const written = page.waitForRequest(
    (r) => r.url().includes('/api/messages/message-2/rating') && r.method() === 'PATCH',
  )
  await down.click()
  expect((await written).postDataJSON()).toEqual({ rating: 'down' })

  // The reload is the test.
  await page.reload()
  const reloaded = page.getByRole('button', { name: '싫어요' })
  await expect(reloaded).toHaveAttribute('aria-pressed', 'true')
  // A rated row is not hidden until hover.
  await expect(reloaded.locator('..')).toHaveCSS('opacity', '1')

  // Pressing again withdraws; `null` travels as a value.
  const withdrawn = page.waitForRequest(
    (r) => r.url().includes('/api/messages/message-2/rating') && r.method() === 'PATCH',
  )
  await reloaded.click()
  expect((await withdrawn).postDataJSON()).toEqual({ rating: null })
  await expect(reloaded).toHaveAttribute('aria-pressed', 'false')
})

// ── 2. 원본 작업 열기 ───────────────────────────────────────────────────

test('원본 작업 열기는 눌린 아티팩트를 연다, 그 대화의 최신 아티팩트가 아니라', async ({ page }, testInfo) => {
  await composerFixture(page, testInfo)

  await stubGet(page, /\/api\/sessions(\?|$)/, [session()])
  await stubGet(page, '**/api/sessions/session-1', session({ messages: [answer(null)] }))
  await stubGet(page, '**/api/artifacts?**', [
    artifact('artifact-clicked', '3월 점검 결과'),
    artifact('artifact-latest', '8월 점검 결과'),
  ])
  await stubGet(page, '**/api/artifacts/counts**', { counts: { html: 2 }, total: 2 })
  await stubGet(page, '**/api/artifacts/artifact-clicked', artifact('artifact-clicked', '3월 점검 결과'))
  await stubGet(page, '**/api/artifacts/artifact-latest', artifact('artifact-latest', '8월 점검 결과'))

  // Every document the app opens, in order.
  const fetched: string[] = []
  page.on('request', (r) => {
    const match = /\/api\/artifacts\/(artifact-[a-z]+)$/.exec(r.url())
    if (match && r.method() === 'GET') fetched.push(match[1])
  })

  await page.goto('/artifacts')
  const links = page.getByRole('button', { name: '원본 작업 열기 →' })
  await expect(links).toHaveCount(2)
  // The older document's card; its session has moved on since.
  await links.first().click()

  // The session's own `artifactId` is `artifact-latest`; that is not what was clicked.
  await expect(page).toHaveURL(/\/s\/session-1/)
  await expect.poll(() => fetched).toContain('artifact-clicked')
  expect(fetched).not.toContain('artifact-latest')

  // The id is spent on arrival, or it would reopen the panel after a deliberate close.
  await expect(page).not.toHaveURL(/artifact=/)
})
