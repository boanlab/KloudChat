import { readFile } from 'node:fs/promises'
import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'
import { openSession, panel, reportSession, resultsFixture } from './results-fixture'
import { shellRead } from './shell-mocks'

/**
 * Screens fixed after a product walk-through, against a mocked API.
 *
 * `SCREEN_SHOTS=<dir>` saves each state as `<name>-<width>.png`.
 */

test.use({ serviceWorkers: 'block' })

const at = '2026-11-14T00:00:00.000Z'
const projectId = '81818181818181818181818181818181'
const chatId = '82828282828282828282828282828282'
const reportChatId = '83838383838383838383838383838383'
const failedId = '84848484848484848484848484848484'
const shots = process.env.SCREEN_SHOTS
/** The whole page at once: the app scrolls inside its own panes, so the window grows to fit them. */
async function wholePage(page: Page, path: string) {
  const size = page.viewportSize()!
  const height = await page.evaluate(() => Math.max(
    document.documentElement.scrollHeight,
    ...[...document.querySelectorAll('*')].filter((el) => {
      const style = getComputedStyle(el)
      return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 4
    }).map((el) => el.scrollHeight + el.getBoundingClientRect().top),
  ))
  await page.setViewportSize({ width: size.width, height: Math.min(Math.ceil(height) + 40, 5000) })
  await page.waitForTimeout(300)
  await page.screenshot({ path })
  await page.setViewportSize(size)
}

/** A studio mind map the size of the walk's: 6 branches, 5 children each, 3 under each child. */
const MINDMAP = [
  'mindmap',
  '  root((클라우드 컴퓨팅 강의))',
  ...['클라우드 기초 개념', '가상화와 컨테이너', '스토리지와 데이터', '네트워크와 확장', '보안과 운영', '비용 구조'].flatMap((branch, b) => [
    `    ${branch}`,
    ...Array.from({ length: 5 }, (_, c) => [
      `      ${branch.split(' ')[0]} 주제 ${c + 1}`,
      ...Array.from({ length: 3 }, (_, g) => `        세부 항목 ${b + 1}-${c + 1}-${g + 1}`),
    ]).flat(),
  ]),
].join('\n')

const FILES = [
  ['f1', '01강_클라우드_컴퓨팅_개요.pdf', 'application/pdf'],
  ['f2', '05강_컨테이너와_Docker.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  ['f3', '기말_프로젝트_안내_및_평가_기준표.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'],
].map(([id, name, mime]) => ({
  id, name, size: 212, mime, tokens: 37, projectId, sessionId: null, sourceUrl: null, origin: 'upload',
  preview: '기말 프로젝트의 팀 구성 규모, 평가 영역별 비중, 제출 마감 일정을 안내한다.', error: null, indexed: true,
  ocrState: null, ocrTotal: 0, ocrDone: 0, ocrReview: 0, createdAt: at,
}))

const TEMPLATES = [
  ['doc-agenda', '회의 안건', '업무'], ['doc-minutes', '회의록', '업무'], ['doc-brief', '간이기안문', '공문'],
  ['doc-proposal', '고객 제안서', '업무'], ['doc-paper', '연구 계획서', '학술'],
].map(([id, name, category]) => ({
  id, kind: 'document', surface: 'report', name, description: `${name} 서식 · 결재란, 개조식 본문, 붙임`,
  category, fills: ['회의 이름', '일시·장소·참석자', '다룰 안건과 담당'], examplePrompt: `${name}을 써 줘.`,
  nameEn: name, descriptionEn: '', categoryEn: category, fillsEn: [], examplePromptEn: '', checks: [],
  formFormat: '', hasPreview: true, slideCount: 0,
}))

type Options = { empty?: boolean; files?: boolean; longModel?: boolean }

function message(id: string, role: 'user' | 'assistant', content: string, extra: Record<string, unknown> = {}) {
  return { id, role, content, steps: null, attachments: null, variants: null, superseded: null, usage: null,
    model: role === 'assistant' ? 'fixture/base' : null, routing: null, startedFrom: null, rating: null,
    artifactIds: null, failure: null, createdAt: at, ...extra }
}

async function fixture(page: Page, info: TestInfo, options: Options = {}) {
  const origin = new URL(String(info.project.use.baseURL)).origin
  const unexpected: string[] = []
  const preview = await readFile(new URL('./fixtures/client-proposal-preview.html', import.meta.url), 'utf8')
  const user = {
    id: 'fixture-user', name: '화면 점검', email: 'fixture@example.test', role: 'user',
    status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
    preferences: { autoMemory: false, showUsage: false, streamResponses: true },
  }
  const session = (id: string, kind: string, title: string, extra: Record<string, unknown>) => ({
    id, kind, title, model: 'fixture/base', routingMode: 'manual', projectId, agentId: null, pipelineId: null,
    artifactId: null, pending: null, renderTemplateId: null, skillIds: [], pinned: false, createdAt: at, updatedAt: at,
    messages: null, preview: null, messageCount: 0, made: null, ...extra,
  })
  const failedMessages = [
    message('q1', 'user', '13강 클라우드 보안과 IAM 내용으로 수업용 슬라이드를 10장 안팎으로 만들어 줘. 마지막에 퀴즈 한 장.'),
    message('a1', 'assistant', '', { failure: 'no_answer' }),
  ]
  const sessions = options.empty ? [] : [
    session(chatId, 'chat', '컨테이너와 Docker 정리', {
      preview: '### 정리\n- **Docker** 이미지는 계층으로 쌓입니다 [1]. 컨테이너는 그 위에 쓰기 계층을 더합니다 [2].', messageCount: 4 }),
    session(reportChatId, 'report', '클라우드 컴퓨팅 강의 노트 마인드맵', { preview: null, messageCount: 0 }),
    session(failedId, 'slides', '13강 클라우드 보안과 IAM', {
      preview: failedMessages[0].content, messageCount: 2 }),
  ]
  const project = {
    id: projectId, name: '클라우드 컴퓨팅 강의', description: '2026학년도 2학기 클라우드 컴퓨팅 수업 자료와 과제', emoji: '📁',
    instructions: '', skillIds: [], designSystemId: null, renderTemplates: {},
    sessionIds: sessions.map((row) => row.id), createdAt: at, updatedAt: at, files: options.files === false ? [] : FILES,
  }
  const studio = [{
    id: 'mm1', projectId, kind: 'mindmap', title: '클라우드 컴퓨팅 강의 노트 마인드맵', state: 'done', error: '',
    sourceIds: ['f1', 'f2', 'f3'], options: {}, artifactId: null, sessionId: null, model: 'fixture/base', credits: 0,
    inputTokens: 900, outputTokens: 300, createdAt: at, updatedAt: at,
    content: { language: 'ko', partial: [], strict: false, sources: [],
      sections: [{ id: 's1', heading: '클라우드 컴퓨팅 강의 노트 마인드맵', content: `\`\`\`mermaid\n${MINDMAP}\n\`\`\`` }] },
  }]
  const models = [
    { id: 'fixture/base', label: 'Qwen3.8 27b',
      name: options.longModel ? 'Qwen3.8 27b Instruct Research Edition' : 'Fixture', vendor: 'Fixture', provider: 'fixture',
      kinds: ['chat', 'report', 'slides'], modality: 'chat', dataBoundary: 'external', creditCost: 1, inputCreditCost: 1,
      supportsTools: true, contextWindow: 64000 },
    { id: 'fixture/flash', label: 'Qwen3.8 Flash', name: 'Flash', vendor: 'Fixture', provider: 'fixture',
      kinds: ['chat', 'report', 'slides'], modality: 'chat', dataBoundary: 'external', creditCost: 1, inputCreditCost: 1,
      supportsTools: true, contextWindow: 64000 },
  ]

  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (path === '/auth/refresh') return json({ accessToken: 'fixture-only', expiresIn: 3600, user })
    if (method === 'GET' && path === '/auth/config') {
      return json({ brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'slides'],
        privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false })
    }
    if (method === 'GET' && path === '/models') {
      return json({ models, defaultChatModel: 'fixture/base', litellmAvailable: false,
        autoRouting: { enabled: false, available: false } })
    }
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (method === 'GET' && path === '/projects') return json(options.empty ? [] : [project])
    if (method === 'GET' && path === `/projects/${projectId}`) return json(project)
    if (method === 'GET' && path === '/sessions') return json(sessions)
    if (method === 'GET' && path === '/sessions/paged') {
      return json({ items: sessions, total: sessions.length, page: 1, size: Number(url.searchParams.get('size') ?? 50) })
    }
    if (method === 'GET' && path === `/sessions/${failedId}`) {
      return json({ ...sessions.find((row) => row.id === failedId), messages: failedMessages })
    }
    if (method === 'GET' && path === `/sessions/${failedId}/messages`) return json(failedMessages)
    if (method === 'GET' && path === `/projects/${projectId}/shelf`) {
      return json({ projectId, sources: FILES.map((f) => ({ ...f, kind: 'file', digest: null, guideState: 'none', readable: true })),
        strictLocal: false, kinds: [] })
    }
    if (method === 'GET' && path.startsWith(`/projects/${projectId}/shelf/guide/`)) {
      return json({ state: 'done', summary: '기말 프로젝트의 팀 구성 규모, 평가 영역별 비중, 제출 마감 일정을 안내한다.',
        topics: ['팀 구성 인원', '평가 영역 및 배점'], questions: ['평가 영역별 배점은 어떻게 되나요?'], model: 'fixture/base' })
    }
    if (method === 'GET' && path === `/projects/${projectId}/studio`) return json(studio)
    if (method === 'GET' && path === `/projects/${projectId}/notes`) return json([])
    if (method === 'GET' && path === '/design-templates') return json(TEMPLATES)
    if (method === 'GET' && /^\/design-templates\/[\w-]+\/preview$/.test(path)) {
      return route.fulfill({ body: preview, contentType: 'text/html; charset=utf-8' })
    }
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && (/^\/sessions\/[0-9a-f]{32}\/(jobs|runs|sources)$/.test(path) || ['/agents', '/skills',
      '/memory', '/tools', '/connectors', '/connectors/store', '/designs', '/shares', '/artifacts', '/jobs',
      '/pipelines', '/user-pipelines', '/files', '/user-templates'].includes(path))) return json([])
    unexpected.push(`${method} ${path}`)
    return json({ detail: 'Unmocked fixture request' }, 501)
  })
  return { unexpected }
}

const width = (page: Page) => page.viewportSize()?.width ?? 1440

/** The viewport, the whole page (`'full'`), or one element scrolled into view. */
async function shot(page: Page, name: string, target?: Locator | 'full') {
  if (!shots) return
  const path = `${shots}/${name}-${width(page)}.png`
  if (target === 'full') return void (await wholePage(page, path))
  if (target) {
    await target.scrollIntoViewIfNeeded()
    await page.waitForTimeout(300)
    return void (await target.screenshot({ path }))
  }
  await page.screenshot({ path })
}

/** Nothing on the page is wider than the screen: no sideways scroll, no control cut at the edge. */
async function fitsTheScreen(page: Page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(over, 'page scrolls sideways').toBeLessThanOrEqual(0)
}

async function inside(locator: Locator, page: Page) {
  const box = await locator.boundingBox()
  expect(box, 'element on screen').not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(width(page) + 0.5)
}

/** Lines a block of text takes: one syllable per line shows as many lines for a short text. */
async function lines(locator: Locator): Promise<number> {
  return locator.evaluate((node) => {
    const range = document.createRange()
    range.selectNodeContents(node)
    return new Set([...range.getClientRects()].filter((r) => r.width > 0).map((r) => Math.round(r.bottom))).size
  })
}

test('@phone a project conversation row reads its latest message, not 「내용 없음」', async ({ page }, info) => {
  const state = await fixture(page, info)
  await page.goto(`/projects/${projectId}`)
  const list = page.getByText('컨테이너와 Docker 정리').locator('xpath=ancestor::*[contains(@class,"cursor-pointer")][1]')
  // one sentence, the first
  await expect(list).toContainText('Docker 이미지는 계층으로 쌓입니다.', { timeout: 15_000 })
  await expect(list).not.toContainText('쓰기 계층을 더합니다')
  await expect(list).not.toContainText('###')
  await expect(list).not.toContainText('[1]')
  const made = page.getByText('클라우드 컴퓨팅 강의 노트 마인드맵').locator('xpath=ancestor::*[contains(@class,"cursor-pointer")][1]')
  await expect(made).toContainText('보고서')
  await expect(page.getByText('내용 없음')).toHaveCount(0)
  await shot(page, 'project-work', 'full')
  expect(state.unexpected).toEqual([])
})

test('@phone the knowledge guidance names every kind the file picker accepts', async ({ page }, info) => {
  const state = await fixture(page, info, { files: false })
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: /^지식/ }).click()
  const guidance = page.getByText(/파일을 올려 두면 이 프로젝트의 모든 대화에서 참조합니다/)
  for (const kind of ['PDF', 'Word', 'PowerPoint', '한글', 'CSV', '그림', '음성', '영상']) await expect(guidance).toContainText(kind)
  const accept = (await page.getByLabel('지식 파일 선택').getAttribute('accept')) ?? ''
  for (const type of ['.pdf', '.docx', '.pptx', '.hwp', '.csv', '.md', 'image/*', 'audio/*', 'video/*']) {
    expect(accept.split(',')).toContain(type)
  }
  await shot(page, 'knowledge-empty')
  expect(state.unexpected).toEqual([])
})

test('the knowledge guidance reads in English', async ({ page }, info) => {
  await fixture(page, info, { files: false })
  await page.addInitScript(() => localStorage.setItem('kchat-lang', 'en'))
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: /^Knowledge/ }).click()
  await expect(page.getByText(/^Upload PDF, Word, PowerPoint, HWP, Excel, CSV, .* and video files and every conversation/)).toBeVisible()
  await shot(page, 'knowledge-empty-en')
})

test('@phone an admin address shows 접근 제한 to a member, with a way home', async ({ page }, info) => {
  const state = await fixture(page, info)
  for (const route of ['/admin/users', '/admin/system']) {
    await page.goto(route)
    await expect(page.getByRole('heading', { name: '이 페이지에 접근할 수 없습니다.' })).toBeVisible({ timeout: 15_000 })
    await expect(page).toHaveURL(new RegExp(`${route}$`))
    await shot(page, `admin-denied-${route.split('/').pop()}`)
  }
  await page.getByRole('button', { name: '홈으로' }).click()
  await expect(page).toHaveURL(/\/$/)
  expect(state.unexpected).toEqual([])
})

test('@phone an empty 대화 기록 offers nothing to pick or delete', async ({ page }, info) => {
  const state = await fixture(page, info, { empty: true })
  await page.goto('/history')
  await expect(page.getByRole('main').getByText('아직 대화가 없습니다')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: '이 페이지 모두 선택' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /선택 \d+개 삭제/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '모든 대화 삭제' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '새 대화 시작' })).toBeVisible()
  await shot(page, 'history-empty')
  expect(state.unexpected).toEqual([])
})

test('a full history still offers its bulk actions', async ({ page }, info) => {
  await fixture(page, info)
  await page.goto('/history')
  await expect(page.getByRole('button', { name: '이 페이지 모두 선택' })).toBeEnabled()
  await expect(page.getByRole('button', { name: '모든 대화 삭제' })).toBeEnabled()
})

test('a large mind map reads as a tree in the studio reader, its branches opening on a click', async ({ page }, info) => {
  const state = await fixture(page, info)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: '스튜디오' }).click()
  await page.locator('[data-studio-item="mm1"]').getByRole('button', { name: /마인드맵/ }).first().click()
  const map = page.getByRole('dialog').getByTestId('mindmap')
  await expect(map).toBeVisible({ timeout: 15_000 })
  await expect(map.getByRole('treeitem', { name: '클라우드 컴퓨팅 강의', exact: true })).toBeVisible()
  // branches and their children at text size; the third level waits for a click
  await expect(map.locator('[data-mindmap-depth="1"]')).toHaveCount(6)
  await expect(map.locator('[data-mindmap-depth="2"]')).toHaveCount(30)
  await expect(map.locator('[data-mindmap-depth="3"]')).toHaveCount(0)
  const size = await map.getByText('클라우드 주제 1', { exact: true }).evaluate((node) => parseFloat(getComputedStyle(node).fontSize))
  expect(size).toBeGreaterThanOrEqual(12)
  await shot(page, 'mindmap-reader')
  await map.getByRole('button', { name: /가상화와 주제 2/ }).click()
  await expect(map.getByText('세부 항목 2-2-3', { exact: true })).toBeVisible()
  await map.getByTestId('mindmap-toggle-all').click()
  await expect(map.locator('[data-mindmap-depth="3"]')).toHaveCount(90)
  await expect(map.getByTestId('mindmap-toggle-all')).toHaveText('모두 접기')
  await map.getByTestId('mindmap-toggle-all').click()
  await expect(map.locator('[data-mindmap-depth="2"]')).toHaveCount(0)
  await expect(map.locator('[data-mindmap-depth="1"]')).toHaveCount(6)
  await map.getByTestId('mindmap-toggle-all').click()
  await map.getByText('세부 항목 6-5-3', { exact: true }).scrollIntoViewIfNeeded()
  await shot(page, 'mindmap-reader-open')
  expect(state.unexpected).toEqual([])
})

test('@phone a failed answer keeps its reason readable, the actions under it', async ({ page }, info) => {
  await fixture(page, info)
  for (const phone of [390, 360]) {
    await page.setViewportSize({ width: phone, height: 844 })
    await page.goto(`/s/${failedId}`)
    const failure = page.getByTestId('answer-failure')
    await expect(failure).toContainText('답변을 받지 못했습니다. 잠시 뒤 다시 시도하세요.', { timeout: 15_000 })
    const reason = failure.getByTestId('answer-failure-reason')
    expect(await lines(reason.locator('span').last())).toBeLessThanOrEqual(2)
    // measured in one go: the row is still sliding in
    await expect.poll(() => failure.evaluate((box) => {
      const reasonBox = box.querySelector('[data-testid="answer-failure-reason"]')!.getBoundingClientRect()
      const retryBox = [...box.querySelectorAll('button')].find((b) => b.textContent?.includes('다시 시도'))!.getBoundingClientRect()
      return reasonBox.height > 0 && retryBox.top >= reasonBox.bottom - 1
    })).toBe(true)
    await fitsTheScreen(page)
    await shot(page, 'answer-failed', 'full')
  }
})

test('a failed answer keeps its actions beside a short reason on a wide screen', async ({ page }, info) => {
  await fixture(page, info)
  await page.goto(`/s/${failedId}`)
  const failure = page.getByTestId('answer-failure')
  await expect(failure).toContainText('답변을 받지 못했습니다. 잠시 뒤 다시 시도하세요.', { timeout: 15_000 })
  // side by side: the button's top within the reason's height (polled: the row slides in)
  await expect.poll(() => failure.evaluate((box) => {
    const reasonBox = box.querySelector('[data-testid="answer-failure-reason"]')!.getBoundingClientRect()
    const retryBox = [...box.querySelectorAll('button')].find((b) => b.textContent?.includes('다시 시도'))!.getBoundingClientRect()
    return reasonBox.height > 0 && Math.abs(retryBox.top - reasonBox.top) < reasonBox.height
  })).toBe(true)
  await shot(page, 'answer-failed', 'full')
})

test('@phone a knowledge row keeps its name and size on one line, the actions under it', async ({ page }, info) => {
  await fixture(page, info)
  for (const phone of [390, 360]) {
    await page.setViewportSize({ width: phone, height: 844 })
    await page.goto(`/projects/${projectId}`)
    await page.getByRole('tab', { name: /^지식/ }).click()
    const row = page.locator('[data-knowledge="f3"]')
    const name = row.getByTestId('knowledge-name')
    const nameBox = (await name.boundingBox())!
    expect(nameBox.width).toBeGreaterThan(phone * 0.6)
    // the size line: 「212 B · 37 토큰 · …」 on one or two lines, not a character per line
    expect(await lines(name.locator('span').first())).toBeLessThanOrEqual(2)
    const actions = row.getByTestId('knowledge-actions')
    const under = await row.evaluate((card) => card.querySelector('[data-testid="knowledge-actions"]')!.getBoundingClientRect().top -
      card.querySelector('[data-testid="knowledge-name"]')!.getBoundingClientRect().bottom)
    expect(under).toBeGreaterThanOrEqual(-1)
    await inside(actions, page)
    await fitsTheScreen(page)
    await shot(page, 'knowledge-rows', 'full')
    await row.getByRole('button', { name: '자료 안내' }).click()
    await expect(row.getByText('평가 영역별 배점은 어떻게 되나요?')).toBeVisible()
    await shot(page, 'knowledge-guide', row)
    await row.getByRole('button', { name: '읽은 내용 확인' }).click()
    await expect(row.getByTestId('knowledge-preview')).toBeVisible()
    await shot(page, 'knowledge-read', row)
  }
})

test('@phone the project boxes keep their text readable beside or above their buttons', async ({ page }, info) => {
  await fixture(page, info)
  for (const [phone, lang] of [[390, 'en'], [360, 'ko'], [390, 'ko']] as const) {
    await page.setViewportSize({ width: phone, height: 844 })
    await page.addInitScript((value) => localStorage.setItem('kchat-lang', value), lang)
    await page.goto(`/projects/${projectId}`)
    const heading = page.getByText(lang === 'en' ? 'Bring in existing work' : '기존 대화 편입', { exact: true })
    await expect(heading).toBeVisible({ timeout: 15_000 })
    expect(await lines(heading)).toBeLessThanOrEqual(1)
    const text = heading.locator('xpath=following-sibling::p[1]')
    expect((await text.boundingBox())!.width).toBeGreaterThan(phone * 0.6)
    await fitsTheScreen(page)
    await shot(page, `project-bring-in-${lang}`, heading.locator('xpath=ancestor::div[contains(@class,"border-dashed")][1]'))
    await page.getByRole('tab', { name: lang === 'en' ? /^Knowledge/ : /^지식/ }).click()
    const files = page.getByText(lang === 'en' ? 'Reference files' : '참고 파일', { exact: true })
    expect((await files.locator('xpath=..').boundingBox())!.width).toBeGreaterThan(phone * 0.4)
    await fitsTheScreen(page)
    await shot(page, `project-knowledge-head-${lang}`, files.locator('xpath=ancestor::div[contains(@class,"border-dashed")][1]'))
  }
})

test('@phone a large mind map reads as an indented tree on a phone', async ({ page }, info) => {
  await fixture(page, info)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: '스튜디오' }).click()
  await page.locator('[data-studio-item="mm1"]').getByRole('button', { name: /마인드맵/ }).first().click()
  const map = page.getByRole('dialog').getByTestId('mindmap')
  await expect(map.locator('[data-mindmap-depth="2"]')).toHaveCount(30, { timeout: 15_000 })
  const label = map.getByText('스토리지와 데이터', { exact: true })
  await inside(label, page)
  const size = await label.evaluate((node) => parseFloat(getComputedStyle(node).fontSize))
  expect(size).toBeGreaterThanOrEqual(12)
  await fitsTheScreen(page)
  await shot(page, 'mindmap-phone')
  await label.scrollIntoViewIfNeeded()
  await shot(page, 'mindmap-phone-scrolled')
})

test('@phone a large mind map reads as a tree in the document panel too', async ({ page }, info) => {
  await resultsFixture(page, info, { reportSections: [
    { heading: '클라우드 컴퓨팅 강의 노트 마인드맵', content: `\`\`\`mermaid\n${MINDMAP}\n\`\`\`` },
    { heading: '구조', content: '**클라우드 컴퓨팅**\n\n- 클라우드 기초 개념\n  - NIST 다섯 가지 본질적 특성' },
  ] })
  await openSession(page, reportSession)
  const map = panel(page).getByTestId('mindmap')
  await expect(map).toBeVisible({ timeout: 15_000 })
  await expect(map.locator('[data-mindmap-depth="2"]')).toHaveCount(30)
  const size = await map.getByText('보안과 운영', { exact: true }).evaluate((node) => parseFloat(getComputedStyle(node).fontSize))
  expect(size).toBeGreaterThanOrEqual(12)
  await map.scrollIntoViewIfNeeded()
  await shot(page, 'mindmap-document')
})

test('@phone the chips under a new report\'s composer stay inside the page gutter', async ({ page }, info) => {
  await fixture(page, info, { longModel: true })
  for (const phone of [390, 360]) {
    await page.setViewportSize({ width: phone, height: 844 })
    await page.goto(`/projects/${projectId}`)
    await page.getByRole('button', { name: '새로 시작' }).click()
    await page.getByRole('menuitem', { name: '보고서' }).click()
    const row = page.getByTestId('composer-phone-row')
    await expect(row).toBeVisible({ timeout: 15_000 })
    const edges = await row.evaluate((node) => {
      const kids = [...node.querySelectorAll('button')].map((b) => b.getBoundingClientRect()).filter((r) => r.width > 0)
      return { left: Math.min(...kids.map((r) => r.left)), right: Math.max(...kids.map((r) => r.right)) }
    })
    expect(edges.left).toBeGreaterThanOrEqual(15)
    expect(edges.right).toBeLessThanOrEqual(phone - 15)
    await fitsTheScreen(page)
    await shot(page, 'composer-chips', row.locator('xpath=..'))
    await shot(page, 'composer-chips-page', 'full')
  }
})
