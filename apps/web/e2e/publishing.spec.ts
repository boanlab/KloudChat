import { expect, test, type Page, type Route } from '@playwright/test'
import { hubFixture, myAgents } from './customize-fixture'

/**
 * 공개 챗봇, phase 1 (members only), against fixture data: the publish dialog, the bot's page
 * (notice, streaming, 👍👎, 만든 사람에게 보내기, paused), the owner's dashboard, the members'
 * list, and the same screens in English. Screenshots go to PUBLISHING_SHOTS when it is set.
 */

const shots = process.env.PUBLISHING_SHOTS ?? ''
const at = '2026-10-10T07:00:00.000Z'
const AGENT = myAgents[0].id as string
const PUB = 'p1111111111111111111111111111111'
const SLUG = 'haksa-1a2b3c'
const SID = 's1111111111111111111111111111111'

const limits = { perVisitorPerMinute: 10, perBotPerMinute: 60, turnsPerDay: 2000, tokensPerDay: 4000000,
  maxInputChars: 8000, turnsPerConversation: 100, concurrency: 4 }

function publication(extra: Record<string, unknown> = {}) {
  return {
    id: PUB, slug: SLUG, title: '학사 안내', description: '학칙과 학사 일정을 안내합니다.', agentId: AGENT, agentName: '논문 리뷰어',
    ownerName: 'Fixture', visibility: 'internal', status: 'approved', pauseReason: '', activeVersionId: 'v2',
    branding: { greeting: '학칙을 찾아 근거 조항과 함께 답합니다.', suggestedPrompts: ['휴학은 언제까지 신청하나요?'], placeholder: '' },
    showProcess: 'summary', showSources: true, retentionDays: 30, conversationView: 'off', consentVersion: 1,
    limits, defaults: limits, createdAt: at, updatedAt: at, publishedAt: at,
    versions: [
      { id: 'v2', number: 2, model: 'strict-local/qwen', note: '2026학년도 학칙', active: true, createdAt: at,
        summary: { knowledge: [{ name: '학칙.pdf', chars: 12000 }] } },
      { id: 'v1', number: 1, model: 'strict-local/qwen', note: '', active: false, createdAt: at, summary: { knowledge: [] } },
    ],
    ...extra,
  }
}

const preflight = (extra: Record<string, unknown> = {}) => ({
  ok: true, problems: [], model: { id: 'strict-local/qwen', label: '내부 Qwen' }, tools: ['calculate', 'search_knowledge'],
  checks: ['answer-hold', 'contact-note', 'pii-mask'],
  disabled: ['memory', 'personal', 'files', 'connectors', 'web', 'documents'],
  knowledge: [{ name: '학칙.pdf', chars: 12000, findings: [{ category: 'phone', count: 1 }] }],
  creditsRemaining: 900, existingId: null, defaults: limits, ...extra,
})

function page_(extra: Record<string, unknown> = {}) {
  return {
    slug: SLUG, title: '학사 안내', description: '학칙과 학사 일정을 안내합니다.', ownerName: '학사지원팀 김담당', mine: false,
    status: 'approved', pauseReason: '', branding: publication().branding, showSources: true, showProcess: 'summary',
    conversationView: 'off', retentionDays: 30, consentVersion: 1, consented: false, color: '#0f766e', maxInputChars: 8000,
    ...extra,
  }
}

const ANSWER = '휴학은 학기 개시일 전까지 신청합니다 [1].\n\n### 출처\n- [1] 학칙.pdf · 제12조(휴학) · 3쪽 · 시행 2025. 3. 1.'

/** The bot's routes on top of the hub fixture; every write is recorded. */
async function botRoutes(page: Page, options: { paused?: boolean; english?: boolean } = {}) {
  const writes: { method: string; path: string; body: unknown }[] = []
  let consented = false
  let shared: string | null = null
  let rating: string | null = null
  let asked = false
  const english = options.english
  const bot = () => page_({
    consented, status: options.paused ? 'paused' : 'approved',
    ...(english ? { title: 'Campus Help', description: 'Answers from the campus rules.', ownerName: 'Jane',
      branding: { greeting: 'Ask about the rules.', suggestedPrompts: ['When is the leave deadline?'], placeholder: '' } } : {}),
  })
  const messages = () => asked ? [
    { id: 'm1', role: 'user', content: '휴학은 언제까지 신청하나요?', rating: null, createdAt: at, steps: null },
    { id: 'm2', role: 'assistant', content: ANSWER, rating, createdAt: at, model: 'strict-local/qwen',
      steps: [{ id: 'h1_0', label: '자료 찾기', status: 'done', category: 'tool' }] },
  ] : []
  const conversation = () => ({ id: SID, title: asked ? '휴학 신청 기한' : '', sharedAt: shared, updatedAt: at, messages: messages() })
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    let body: unknown = null
    try {
      body = request.postDataJSON()
    } catch {
      body = null
    }
    if (method !== 'GET') writes.push({ method, path, body })
    if (method === 'GET' && path === '/bots') {
      return json([
        { slug: SLUG, title: english ? 'Campus Help' : '학사 안내', description: english ? 'Answers from the campus rules.' : '학칙과 학사 일정을 안내합니다.',
          ownerName: english ? 'Jane' : '학사지원팀 김담당', mine: false, id: null, status: 'approved', color: '#0f766e', publishedAt: at },
        { slug: 'mine-9z', title: english ? 'My Help Desk' : '내 안내 챗봇', description: '', ownerName: 'Fixture', mine: true, id: PUB,
          status: 'paused', color: '#5b53e8', publishedAt: at },
      ])
    }
    if (path === `/b/${SLUG}` && method === 'GET') return json(bot())
    if (path === `/b/${SLUG}/consent` && method === 'POST') {
      consented = true
      return route.fulfill({ status: 204 })
    }
    if (path === `/b/${SLUG}/conversations` && method === 'GET') return json(asked ? [{ id: SID, title: '휴학 신청 기한', updatedAt: at, sharedAt: shared }] : [])
    if (path === `/b/${SLUG}/conversations` && method === 'POST') return json({ id: SID, title: '', updatedAt: at, sharedAt: null }, 201)
    if (path === `/b/${SLUG}/conversations/${SID}/messages` && method === 'POST') {
      if (options.paused) return json({ detail: 'bot_paused' }, 409)
      asked = true
      const events = [
        { type: 'model', models: ['strict-local/qwen'] },
        { type: 'step', id: 'h1_0', label: '자료 찾기', status: 'running', category: 'tool' },
        { type: 'step', id: 'h1_0', label: '자료 찾기', status: 'done', category: 'tool' },
        { type: 'delta', text: '휴학은 학기 개시일 전까지 신청합니다 [1].' },
        { type: 'delta', text: '\n\n### 출처\n- [1] 학칙.pdf · 제12조(휴학) · 3쪽 · 시행 2025. 3. 1.' },
        { type: 'title', title: '휴학 신청 기한' },
        { type: 'usage', inputTokens: 900, outputTokens: 60 },
        { type: 'done', messageId: 'm2' },
      ]
      return route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' },
        body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') })
    }
    if (path === `/b/${SLUG}/conversations/${SID}` && method === 'GET') return json(conversation())
    if (path === `/b/${SLUG}/messages/m2/rating` && method === 'PATCH') {
      rating = (body as { rating: string | null }).rating
      return json({ id: 'm2', rating })
    }
    if (path === `/b/${SLUG}/conversations/${SID}/share` && method === 'POST') {
      shared = at
      return json({ id: SID, sharedAt: at })
    }
    if (path === `/b/${SLUG}/conversations/${SID}/share` && method === 'DELETE') {
      shared = null
      return json({ id: SID, sharedAt: null })
    }
    if (path === '/publications/preflight' && method === 'POST') return json(preflight())
    if (path === '/publications' && method === 'POST') return json(publication({ versions: [publication().versions[1]] }), 201)
    if (path === `/publications/${PUB}` && method === 'GET') {
      return json(english ? publication({ title: 'Campus Help', agentName: 'Reviewer', versions: publication().versions.map((v) => ({ ...v, note: '' })) }) : publication())
    }
    if (path === `/publications/${PUB}` && method === 'PATCH') return json(publication({ ...(body as object), consentVersion: 2 }))
    if (path === `/publications/${PUB}/stats`) {
      return json({ days: 30, daily: [{ date: '2026-10-10', turns: 12, answers: 11, conversations: 5, visitors: 4, tokens: 9000, unanswered: 2, up: 3, down: 1, shared: 1, failed: 0 }],
        totals: { turns: 12, answers: 11, conversations: 5, visitors: 4, tokens: 9000, unanswered: 2, up: 3, down: 1, shared: 1, failed: 0, visitorsAll: 7 },
        topics: [{ word: english ? 'leave' : '휴학', count: 6 }, { word: english ? 'tuition' : '등록금', count: 3 }],
        unanswered: [{ text: english ? 'Parking fee? call [전화번호]' : '주차 요금은 얼마예요? 제 번호 [전화번호]', at }] })
    }
    if (path === `/publications/${PUB}/conversations` && method === 'GET') {
      return json([{ id: SID, title: english ? 'Leave deadline' : '휴학 신청 기한', sharedAt: at, viewable: false, updatedAt: at, turns: 1 }])
    }
    if (path === `/publications/${PUB}/conversations/${SID}`) {
      return json({ id: SID, title: '휴학 신청 기한', sharedAt: at, updatedAt: at, turns: 1, messages: [
        { id: 'm1', role: 'user', content: english ? 'Deadline? I am at [전화번호]' : '휴학 기한이요? 제 번호는 [전화번호]', rating: null, createdAt: at },
        { id: 'm2', role: 'assistant', content: english ? 'Before term starts [1].' : ANSWER, rating: 'down', createdAt: at },
      ] })
    }
    if (path === `/publications/${PUB}/versions/v1/activate` && method === 'POST') {
      return json(publication({ activeVersionId: 'v1', versions: publication().versions.map((v) => ({ ...v, active: v.id === 'v1' })) }))
    }
    return route.fallback()
  })
  return { writes }
}

async function settle(page: Page) {
  await page.waitForLoadState('networkidle').catch(() => undefined)
  await page.waitForTimeout(300)
}

async function shot(page: Page, name: string, project: string) {
  if (!shots) return
  await settle(page)
  await page.screenshot({ path: `${shots}/${project}-${name}.png`, fullPage: false })
}

test.use({ serviceWorkers: 'block' })

test('the owner publishes a chat agent to members and gets its address', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  await page.goto(`/customize/agents/${AGENT}`)
  await expect(page.getByTestId('item-detail-header')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '더 보기' }).first().click()
  await page.getByRole('menuitem', { name: '챗봇으로 공개' }).click()
  const dialog = page.getByRole('dialog', { name: '챗봇으로 공개' })
  await expect(dialog.getByTestId('publish-model')).toContainText('내부 Qwen')
  await expect(dialog).toContainText('공개 범위: 이 기관 구성원(로그인한 사람)')
  await expect(dialog.getByTestId('publish-disabled')).toContainText('내 메모리')
  await expect(dialog.getByTestId('publish-disabled')).toContainText('웹 검색')
  await expect(dialog.getByTestId('publish-finding')).toContainText('휴대전화 번호 1')
  // Phase 1 is members only: no link or outside option is offered at all.
  await expect(dialog).not.toContainText('링크')
  await expect(dialog).not.toContainText('외부')
  await shot(page, 'publish-dialog', testInfo.project.name)
  await dialog.getByRole('button', { name: '공개하기' }).click()
  await expect(dialog.getByTestId('publish-done')).toBeVisible()
  await expect(dialog.getByTestId('bot-url')).toContainText(`/b/${SLUG}`)
  await shot(page, 'publish-done', testInfo.project.name)
  const sent = writes.find((w) => w.method === 'POST' && w.path === '/publications')?.body as Record<string, unknown>
  expect(sent).toMatchObject({ agentId: AGENT, visibility: 'internal', showProcess: 'summary', conversationView: 'off' })
  expect(state.unexpected).toEqual([])
})

test('without an internal model the dialog says why and publishes nothing', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  await page.route('**/api/publications/preflight', (route) => route.fulfill({ json: preflight({ ok: false, model: null, problems: ['no_local_model'] }) }))
  await page.goto(`/customize/agents/${AGENT}`)
  await expect(page.getByTestId('item-detail-header')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '더 보기' }).first().click()
  await page.getByRole('menuitem', { name: '챗봇으로 공개' }).click()
  const dialog = page.getByRole('dialog', { name: '챗봇으로 공개' })
  await expect(dialog.getByTestId('publish-problem')).toContainText('내부 모델이 없어')
  await expect(dialog.getByRole('button', { name: '공개하기' })).toBeDisabled()
})

test('a member reads the notice, asks, rates and sends the conversation to the maker', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  await page.goto(`/b/${SLUG}`)
  const notice = page.getByTestId('bot-notice')
  await expect(notice).toContainText('입력 내용은 이 챗봇을 만든 사람 계정으로 처리·기록됩니다(개선을 위해 개인정보를 가린 통계로만 보입니다).')
  await expect(notice).toContainText('개인정보를 입력하지 마세요.')
  await expect(page.getByLabel('질문 입력')).toBeDisabled()
  await shot(page, 'bot-notice', testInfo.project.name)
  await notice.getByRole('button', { name: '확인하고 시작' }).click()
  await expect(page.getByTestId('bot-prompts')).toBeVisible()
  await shot(page, 'bot-empty', testInfo.project.name)
  await page.getByLabel('질문 입력').fill('휴학은 언제까지 신청하나요?')
  await page.getByRole('button', { name: '보내기', exact: true }).click()
  await expect(page.getByText('휴학은 학기 개시일 전까지 신청합니다')).toBeVisible()
  await expect(page.getByText('학칙.pdf · 제12조(휴학) · 3쪽 · 시행 2025. 3. 1.')).toBeVisible()
  await expect(page.getByTestId('bot-steps')).toContainText('자료 찾기')
  await page.getByRole('button', { name: '아쉬워요' }).click()
  await expect(page.getByTestId('bot-offer-share')).toBeVisible()
  await shot(page, 'bot-answer', testInfo.project.name)
  await page.getByTestId('bot-offer-share').getByRole('button', { name: '보내기' }).click()
  await expect(page.getByTestId('bot-shared')).toContainText('만든 사람에게 보냈습니다')
  expect(writes.map((w) => `${w.method} ${w.path}`)).toEqual(expect.arrayContaining([
    `POST /b/${SLUG}/consent`, `POST /b/${SLUG}/conversations`, `POST /b/${SLUG}/conversations/${SID}/messages`,
    `PATCH /b/${SLUG}/messages/m2/rating`, `POST /b/${SLUG}/conversations/${SID}/share`,
  ]))
  expect(writes.find((w) => w.path.endsWith('/rating'))?.body).toEqual({ rating: 'down' })
  expect(state.unexpected).toEqual([])
})

test('a paused bot says so and takes no question', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  await botRoutes(page, { paused: true })
  await page.goto(`/b/${SLUG}`)
  await page.getByRole('button', { name: '확인하고 시작' }).click()
  await expect(page.getByTestId('bot-paused')).toContainText('지금은 이 챗봇을 쓸 수 없습니다.')
  await expect(page.getByLabel('질문 입력')).toBeDisabled()
  await expect(page.getByTestId('bot-prompts')).toHaveCount(0)
  await shot(page, 'bot-paused', testInfo.project.name)
})

test('the dashboard shows statistics, sent conversations and versions, and rolls back', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const { writes } = await botRoutes(page)
  await page.goto(`/customize/publications/${PUB}`)
  await expect(page.getByRole('heading', { level: 1, name: '학사 안내' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('publication-url')).toContainText(`/b/${SLUG}`)
  await shot(page, 'dashboard-overview', testInfo.project.name)
  await page.getByRole('tab', { name: '통계' }).click()
  await expect(page.getByTestId('publication-topics')).toContainText('휴학')
  await expect(page.getByTestId('publication-unanswered')).toContainText('[전화번호]')
  await shot(page, 'dashboard-stats', testInfo.project.name)
  await page.getByRole('tab', { name: '보낸 대화' }).click()
  await page.getByTestId('publication-conversations').getByRole('button').first().click()
  await expect(page.getByTestId('publication-transcript')).toContainText('[전화번호]')
  await shot(page, 'dashboard-conversations', testInfo.project.name)
  await page.getByRole('tab', { name: '버전' }).click()
  await expect(page.getByTestId('publication-versions')).toContainText('2판')
  await page.getByRole('button', { name: '이 판으로 되돌리기' }).click()
  await expect(page.getByTestId('publication-versions').getByText('지금 판')).toHaveCount(1)
  await shot(page, 'dashboard-versions', testInfo.project.name)
  await page.getByRole('tab', { name: '설정' }).click()
  await page.getByRole('switch', { name: '대화 열람' }).click()
  await expect(page.getByText('저장하면 방문자가 다음 질문 전에 바뀐 안내를 다시 확인합니다.')).toBeVisible()
  await shot(page, 'dashboard-settings', testInfo.project.name)
  await Promise.all([
    page.waitForRequest((r) => r.method() === 'PATCH' && r.url().endsWith(`/publications/${PUB}`)),
    page.getByRole('button', { name: '저장', exact: true }).click(),
  ])
  expect(writes.find((w) => w.method === 'PATCH')?.body).toMatchObject({ conversationView: 'on' })
  expect(writes.some((w) => w.path.endsWith('/versions/v1/activate'))).toBe(true)
  expect(state.unexpected).toEqual([])
})

test('members find the bots under 챗봇 and owners their own to manage', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await botRoutes(page)
  await page.goto('/bots')
  await expect(page.getByRole('heading', { level: 1, name: '챗봇' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('bot-list')).toContainText('학사 안내')
  await expect(page.getByTestId('my-bots')).toContainText('일시 중지')
  await expect(page.getByTestId('my-bots').getByRole('link', { name: '관리' })).toHaveAttribute('href', `/customize/publications/${PUB}`)
  await shot(page, 'bots', testInfo.project.name)
  await page.getByTestId('bot-list').getByRole('link').first().click()
  await expect(page).toHaveURL(new RegExp(`/b/${SLUG}$`))
  expect(state.unexpected).toEqual([])
})

/** Visible text and labels still in Korean, user content aside. */
async function korean(page: Page, allowed: string[]): Promise<string[]> {
  await settle(page)
  return page.evaluate((allowed) => {
    const out: string[] = []
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const text = (n.textContent || '').trim()
      const el = n.parentElement
      if (!text || !/[가-힣]/.test(text) || !el) continue
      const r = el.getBoundingClientRect()
      if (r.width === 0 && r.height === 0) continue
      if (allowed.some((a) => text.includes(a))) continue
      out.push(text)
    }
    for (const el of Array.from(document.querySelectorAll('[aria-label],[placeholder],[title]'))) {
      for (const attr of ['aria-label', 'placeholder', 'title']) {
        const text = el.getAttribute(attr) || ''
        if (/[가-힣]/.test(text) && !allowed.some((a) => text.includes(a))) out.push(`[${attr}] ${text}`)
      }
    }
    return out
  }, allowed)
}

test('the publishing screens read in English', async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  await page.addInitScript(() => localStorage.setItem('kchat-lang', 'en'))
  await hubFixture(page, testInfo)
  await botRoutes(page, { english: true })
  // Fixture content in Korean that the screens show as data, not as their own words.
  const data = ['[전화번호]', '학칙.pdf', '제12조', '휴학', '[1]', '내부 Qwen', '논문 리뷰어', '학칙', '학사']
  // A line of Korean that is not data; the language toggle's 「한」 is a mark, not a word.
  const untranslated = (text: string) =>
    text.split('\n').filter((line) => /[가-힣]/.test(line) && line.trim() !== '한' && !data.some((d) => line.includes(d)))

  await page.goto(`/b/${SLUG}`)
  await expect(page.getByTestId('bot-notice')).toBeVisible({ timeout: 15_000 })
  expect(await korean(page, data)).toEqual([])
  await page.getByRole('button', { name: 'Got it, start' }).click()
  await expect(page.getByTestId('bot-prompts')).toBeVisible()
  expect(await korean(page, data)).toEqual([])
  await shot(page, 'en-bot', testInfo.project.name)

  await page.goto('/bots')
  await expect(page.getByRole('heading', { level: 1, name: 'Chatbots' })).toBeVisible({ timeout: 15_000 })
  // The sidebar and shell are checked by their own gates; this screen's main area here.
  expect(untranslated(await page.getByRole('main').innerText())).toEqual([])

  await page.goto(`/customize/publications/${PUB}`)
  await expect(page.getByRole('heading', { level: 1, name: 'Campus Help' })).toBeVisible({ timeout: 15_000 })
  for (const tab of ['Statistics', 'Sent conversations', 'Versions', 'Settings']) {
    await page.getByRole('tab', { name: tab }).click()
    await settle(page)
    expect(untranslated(await page.getByRole('main').innerText()), tab).toEqual([])
  }

  await page.goto(`/customize/agents/${AGENT}`)
  await expect(page.getByTestId('item-detail-header')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Show more' }).first().click()
  await page.getByRole('menuitem', { name: 'Publish as a chatbot' }).click()
  const dialog = page.getByRole('dialog', { name: 'Publish as a chatbot' })
  await expect(dialog.getByTestId('publish-model')).toBeVisible()
  expect(untranslated(await dialog.innerText())).toEqual([])
})
