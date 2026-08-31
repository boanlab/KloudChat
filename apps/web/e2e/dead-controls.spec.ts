import { expect, test, type Page } from '@playwright/test'
import { openSidebar, pickToolModel, signIn } from './helpers'

/**
 * Four controls that must do what they look like they do.
 *
 * None of these failures shows in a screenshot: the thumb lights, the button
 * navigates, the switch turns blue. What is checked is what happens next — the
 * rating survives a reload, the link opens the document it named, the switch
 * scopes to the conversation it was set in, and an imported agent says what
 * did not come with it.
 *
 * Stubbed rather than seeded, deliberately: what is being held here is what
 * the client does with a fixed server, and every one of these bugs is a claim
 * about a request that was or was not made.
 */

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
async function stubGet(page: Page, pattern: string, payload: unknown) {
  await page.route(pattern, async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue()
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

test('싫어요는 서버에 남고, 다시 열었을 때 눌린 채로 보인다', async ({ page }) => {
  await signIn(page)

  let stored: 'up' | 'down' | null = null
  await stubGet(page, '**/api/sessions', [session()])
  await page.route('**/api/sessions/session-1', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue()
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
  // The endpoint the buttons have to reach. It stores, and answers with the
  // message, so the client ends up holding the server's version of the turn.
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

  // The reload is the whole test: a verdict held only in the tab is no verdict.
  await page.reload()
  const reloaded = page.getByRole('button', { name: '싫어요' })
  await expect(reloaded).toHaveAttribute('aria-pressed', 'true')
  // And readable without going looking for it. The row is hidden until the
  // turn is hovered; a rated one is not, or recording the rating bought
  // nobody anything.
  await expect(reloaded.locator('..')).toHaveCSS('opacity', '1')

  // Pressing it again is a withdrawal, and `null` has to travel as a value.
  const withdrawn = page.waitForRequest(
    (r) => r.url().includes('/api/messages/message-2/rating') && r.method() === 'PATCH',
  )
  await reloaded.click()
  expect((await withdrawn).postDataJSON()).toEqual({ rating: null })
  await expect(reloaded).toHaveAttribute('aria-pressed', 'false')
})

// ── 2. 원본 작업 열기 ───────────────────────────────────────────────────

test('원본 작업 열기는 눌린 결과물을 연다, 그 대화의 최신 결과물이 아니라', async ({ page }) => {
  await signIn(page)

  await stubGet(page, '**/api/sessions', [session()])
  await stubGet(page, '**/api/sessions/session-1', session({ messages: [answer(null)] }))
  await stubGet(page, '**/api/artifacts?**', [
    artifact('artifact-clicked', '3월 점검 결과'),
    artifact('artifact-latest', '8월 점검 결과'),
  ])
  await stubGet(page, '**/api/artifacts/counts**', { counts: { html: 2 }, total: 2 })
  await stubGet(page, '**/api/artifacts/artifact-clicked', artifact('artifact-clicked', '3월 점검 결과'))
  await stubGet(page, '**/api/artifacts/artifact-latest', artifact('artifact-latest', '8월 점검 결과'))

  // Every document the app decides to open, in the order it asks for them.
  const fetched: string[] = []
  page.on('request', (r) => {
    const match = /\/api\/artifacts\/(artifact-[a-z]+)$/.exec(r.url())
    if (match && r.method() === 'GET') fetched.push(match[1])
  })

  await page.goto('/artifacts')
  const links = page.getByRole('button', { name: '원본 작업 열기 →' })
  await expect(links).toHaveCount(2)
  // The stub lists 3월 first, so this is the older document's card — the one
  // whose session has moved on since.
  await links.first().click()

  // The session's own `artifactId` is `artifact-latest`. Opening that instead
  // would be a different document three turns later, and nothing at all for a
  // session whose result has been deleted.
  await expect(page).toHaveURL(/\/s\/session-1/)
  await expect.poll(() => fetched).toContain('artifact-clicked')
  expect(fetched).not.toContain('artifact-latest')

  // The id is spent on arrival. Left in the URL it would reopen the panel
  // every time the session re-rendered, including after a deliberate close.
  await expect(page).not.toHaveURL(/artifact=/)
})

// ── 3. 웹 검색 ─────────────────────────────────────────────────────────

test('웹 검색은 켠 대화에만 남고 다음 대화로 따라오지 않는다', async ({ page }) => {
  await signIn(page)

  await stubGet(page, '**/api/sessions', [session()])
  await stubGet(page, '**/api/sessions/session-1', session({ messages: [answer(null)] }))

  await page.goto('/new/chat')
  // The screen default is strict-local, which is given no web tool — so the
  // toggle this test is about is disabled until a model that can reach the
  // network is chosen.
  await pickToolModel(page)

  const search = page.getByRole('button', { name: '웹 검색' })
  await search.click()
  await expect(search).toHaveAttribute('aria-pressed', 'true')

  // The same control bar, one conversation over — reached from the sidebar
  // rather than by `goto`, because a full navigation remounts the composer and
  // would clear the switch whether or not anything here was fixed. The
  // activated skills and the attachments beside it have always been cleared on
  // this move; this was the switch that was not, and it spends the next
  // conversation's credits on a search nobody asked for in it.
  await openSidebar(page)
  await page.getByRole('button', { name: '보안 점검 보고서' }).first().click()
  await expect(page).toHaveURL(/\/s\/session-1/)
  await expect(page.getByRole('button', { name: '웹 검색' })).toHaveAttribute(
    'aria-pressed',
    'false',
  )
})

// ── 4. 에이전트 가져오기 ────────────────────────────────────────────────

test('공유된 에이전트를 가져오면 서버가 사본을 만들고 함께 오지 않은 것을 밝힌다', async ({ page }) => {
  await signIn(page)
  // 내 것/스토어를 가르는 기준은 실제 계정 id 다 — 목의 'me' 는 어느 탭에도
  // 서지 못해 사본이 화면에서 사라진다.
  const myId = await page.evaluate(async () => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = (await login.json()) as { accessToken: string }
    const r = await fetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + accessToken } })
    return ((await r.json()) as { id: string }).id
  })

  const shared = {
    ownerId: 'someone-else',
    ownerName: '김보안',
    id: 'agent-shared',
    name: '보고서 검토자',
    slug: 'report-reviewer',
    description: '보고서를 규정에 맞게 검토합니다',
    model: 'external/one',
    systemPrompt: '너는 검토자다',
    tools: null,
    // Rows in the other account. The install route copies the shared ones and
    // rewrites this list against the copies — which is why the copy below
    // carries an id of its own rather than this one.
    skillIds: ['skill-theirs'],
    kinds: ['chat'],
    temperature: 0.7,
    color: '#5b53e8',
    enabled: true,
    visibility: 'org',
    installs: 3,
    catalogKey: null,
    originId: null,
    official: false,
    installed: false,
    runs: 12,
    hasKnowledge: false,
    updatedAt: NOW,
  }
  const copy = {
    ...shared,
    id: 'agent-mine',
    ownerId: myId,
    ownerName: '나',
    visibility: 'private',
    installs: 0,
    originId: 'agent-shared',
    skillIds: ['skill-mine'],
    description: `${shared.description} · 지식 문서는 원본 소유자의 것이라 함께 오지 않습니다. 직접 올려 주세요.`,
  }

  let installed = false
  await page.route('**/api/agents', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(
        installed ? [{ ...shared, installed: true }, copy] : [shared],
      ),
    })
  })
  // The copy is made server-side now: the allow-list is a list of rows in the
  // author's account, and only the server can install their shared skills and
  // point the copy at the results.
  await page.route('**/api/agents/agent-shared/install', async (route) => {
    installed = true
    await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(copy) })
  })

  await page.goto('/agents')
  // 남의 공유 에이전트는 이제 내 에이전트가 아니라 스토어에 선다 — 내
  // 목록에 보기만 되는 카드가 섞여 있던 것이 고쳐진 자리다.
  await page.getByRole('tab', { name: /워크스페이스 스토어/ }).click()
  await page.getByRole('button', { name: '가져오기' }).click()

  await expect.poll(() => installed).toBe(true)
  // The card it came from stops offering an import that is already done.
  await expect(page.getByRole('button', { name: '가져옴' })).toBeVisible()
  // Said where the copy lands, not in a toast: the question it answers — why
  // is my copy worse than the one I tried? — is asked days afterwards. The
  // copy is mine, so it stands on the 내 에이전트 tab.
  await page.getByRole('tab', { name: /내 에이전트/ }).click()
  await expect(
    page.getByText('지식 문서는 원본 소유자의 것이라 함께 오지 않습니다. 직접 올려 주세요.'),
  ).toBeVisible()
})
