import { expect, test, type Page, type TestInfo } from '@playwright/test'
import style from './fixtures/report-table-style.json' with { type: 'json' }
import { shellRead } from './shell-mocks'

/**
 * The web report is one Markdown document: one editor over every section, a
 * 「Markdown 원문」 view of the same text, and saves that split it back into the stored
 * sections by their `##` headings, ids kept. No backend: every call is answered here.
 */

test.use({ serviceWorkers: 'block' })

const sessionId = '31111111111111111111111111111111'
const artifactId = '32222222222222222222222222222222'
const at = '2026-11-13T00:00:00.000Z'
const title = '분기 점검 보고서'
const sections = [
  { id: 's1', heading: '개요', level: 1, status: 'done', content: '첫 절 본문 [1].\n\n## 소제목\n\n- 항목 가\n- 항목 나' },
  { id: 's2', heading: '분석', level: 1, status: 'done', content: '| 기준 | 값 |\n| --- | --- |\n| 비용 | 3억 |' },
  { id: 's3', heading: '결론', level: 1, status: 'done', content: '담당: [담당자]' },
]
const markdown = `# ${title}\n\n## 개요\n\n첫 절 본문 [1].\n\n### 소제목\n\n- 항목 가\n- 항목 나\n\n## 분석\n\n| 기준 | 값 |\n| --- | --- |\n| 비용 | 3억 |\n\n## 결론\n\n담당: [담당자]\n`

type Section = (typeof sections)[number] & { format?: string }

async function fixture(page: Page, testInfo: TestInfo, extra: Record<string, unknown> = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const writes: { data: { sections: Section[] }; title?: string; expectedVersion: number }[] = []
  let artifact = {
    id: artifactId, title, kind: 'report', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { kind: 'report', title, citationStyle: 'APA', sections: structuredClone(sections) as Section[],
      sources: [{ id: 'src1', ordinal: 1, title: '점검 기준', url: 'https://example.test/a', origin: 'web' }], ...extra },
  }
  let messages: { id: string; role: string; content: string; createdAt: string }[] = []
  const session = () => ({ id: sessionId, title, kind: 'report', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId, pinned: false, messages, messageCount: messages.length,
    made: null, createdAt: at, updatedAt: at })
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) {
      unexpected.push(`external ${url.origin}`)
      return route.abort('blockedbyclient')
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'GET' && path === '/design-templates/_document/style') return route.fulfill({ json: style })
    if (method === 'POST' && path === '/auth/refresh') return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600,
      user: { id: 'fixture-user', name: 'Report fixture', email: 'fixture@example.test', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
        createdAt: at, preferences: { autoMemory: false, showUsage: false, streamResponses: true } } } })
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' },
      enabledKinds: ['chat', 'report'], privacy: { externalDataGuard: false }, passwordResetEnabled: false,
      dictationEnabled: false } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: { models: [{ id: 'fixture/base', label: 'Fixture',
      name: 'Fixture', vendor: 'Fixture', provider: 'fixture', kinds: ['chat', 'report'], modality: 'chat',
      dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, supportsTools: true, contextWindow: 64000 }],
      defaultChatModel: 'fixture/base', litellmAvailable: false, autoRouting: { enabled: false, available: false } } })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [session()] })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session() })
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: [artifact] })
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return route.fulfill({ json: artifact })
    if (method === 'PATCH' && path === `/artifacts/${artifactId}`) {
      const patch = request.postDataJSON()
      // conditional on the version the editor started from
      if (patch.expectedVersion !== artifact.version) {
        return route.fulfill({ status: 409, json: { detail: '이 아티팩트는 다른 곳에서 이미 수정되었습니다.' } })
      }
      writes.push(patch)
      artifact = { ...artifact, title: patch.title ?? artifact.title, data: patch.data, version: artifact.version + 1 }
      return route.fulfill({ json: artifact })
    }
    if (method === 'POST' && path === `/sessions/${sessionId}/messages`) {
      // A chat edit: the server rewrites one section as Markdown and stores a new version.
      const body = request.postDataJSON()
      const revised = artifact.data.sections.map((row) =>
        row.id === 's2' ? { ...row, content: '| 기준 | 값 |\n| --- | --- |\n| 비용 | 2억 |' } : row)
      artifact = { ...artifact, version: artifact.version + 1, data: { ...artifact.data, sections: revised } }
      const answer = '분석 절의 비용을 2억으로 고쳤습니다.'
      messages = [...messages,
        { id: `q${messages.length}`, role: 'user', content: String(body.content), createdAt: at },
        { id: `a${messages.length}`, role: 'assistant', content: answer, createdAt: at }]
      const events = [
        { type: 'delta', text: answer },
        { type: 'artifact', artifactId, deliberate: true },
        { type: 'usage', inputTokens: 10, outputTokens: 5, credits: 1 },
        { type: 'done' },
      ]
      return route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
        body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: { report: 1 }, total: 1 } })
    if (method === 'GET' && path === `/artifacts/${artifactId}/versions`) return route.fulfill({ json: [] })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && [`/sessions/${sessionId}/messages`, `/sessions/${sessionId}/jobs`, '/projects',
      '/skills', '/memory', '/agents', '/tools', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/design-templates', '/shares', '/user-pipelines', '/skills/store'].includes(path)) return route.fulfill({ json: [] })
    // Drawn diagrams are posted back; nothing to store here.
    if (method === 'POST' && path.startsWith(`/artifacts/${artifactId}/sections/`)) return route.fulfill({ json: artifact })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  /** Another tab saves a new version. */
  const bump = () => {
    artifact = { ...artifact, version: artifact.version + 1, data: { ...artifact.data,
      sections: artifact.data.sections.map((row) => (row.id === 's1' ? { ...row, content: '다른 사람이 고친 본문' } : row)) } }
  }
  return { unexpected, writes, bump, stored: () => structuredClone(artifact) }
}

const panel = (page: Page) => page.locator('[data-panel="artifact"]')
const editor = (page: Page) => page.locator('.ProseMirror').first()

async function enterEditor(page: Page) {
  await panel(page).locator('header').getByRole('button', { name: '편집', exact: true }).click()
  await expect(editor(page)).toBeVisible()
  await expect(editor(page).getByRole('heading', { name: '분석' })).toBeVisible()
}

async function save(page: Page) {
  const button = page.getByRole('button', { name: '저장', exact: true })
  await expect(page.getByText('저장 안 됨')).toBeVisible()
  await button.click()
  await expect(button).toBeDisabled()
  await expect(page.getByText('저장 안 됨')).toHaveCount(0)
}

async function shot(page: Page, name: string) {
  const dir = process.env.REPORT_MD_SHOTS
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` })
}

test('one editor holds every section; deleting across a section boundary merges them', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  // One editor, not one per section.
  await expect(page.locator('.ProseMirror')).toHaveCount(1)
  await expect(editor(page).locator('h2')).toHaveText(['개요', '분석', '결론'])

  // Caret at the start of 「분석」, then the selection runs back into the list above it.
  await editor(page).getByRole('heading', { name: '분석' }).click()
  await page.keyboard.press('Home')
  await page.keyboard.press('Shift+ArrowLeft')
  await page.keyboard.press('Shift+ArrowLeft')
  await page.keyboard.type('다')
  await expect(editor(page).locator('h2')).toHaveText(['개요', '결론'])
  await save(page)

  const saved = state.writes[0].data.sections
  expect(saved.map((row) => row.id)).toEqual(['s1', 's3'])
  expect(saved[0].content).toBe('첫 절 본문 [1].\n\n## 소제목\n\n- 항목 가\n- 항목 다분석\n\n| 기준 | 값 |\n| --- | --- |\n| 비용 | 3억 |')
  expect(saved.every((row) => row.format === 'markdown')).toBe(true)
  // The untouched section is stored exactly as it was.
  expect(saved[1].content).toBe(sections[2].content)
  expect(state.unexpected).toEqual([])
})

test('typing a ## heading starts a new section with its own id', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  await editor(page).getByText('담당:').click()
  await page.keyboard.press('End')
  await page.keyboard.press('Enter')
  await page.keyboard.type('## 새 절')
  await page.keyboard.press('Enter')
  await page.keyboard.type('새 본문입니다.')
  await expect(editor(page).locator('h2')).toHaveText(['개요', '분석', '결론', '새 절'])
  await save(page)

  const saved = state.writes[0].data.sections
  expect(saved.map((row) => row.heading)).toEqual(['개요', '분석', '결론', '새 절'])
  expect(saved.slice(0, 3).map((row) => row.id)).toEqual(['s1', 's2', 's3'])
  expect(saved[3].id).toMatch(/^s_/)
  expect(saved[3].content).toBe('새 본문입니다.')
  expect(saved[2].content).toBe('담당: [담당자]')
  expect(state.unexpected).toEqual([])
})

test('「Markdown 원문」 shows the same document and reads edits back', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  await page.getByRole('tab', { name: '보기' }).click()
  const toggle = page.getByRole('button', { name: 'Markdown 원문' })
  await toggle.click()
  const source = page.getByLabel('문서 원본')
  await expect(source).toHaveValue(markdown)
  await shot(page, 'markdown-source-1440')

  // There and back unchanged: nothing to save.
  await toggle.click()
  await expect(source).toBeHidden()
  await expect(editor(page).locator('h2')).toHaveText(['개요', '분석', '결론'])
  await expect(page.getByRole('button', { name: '저장', exact: true })).toBeDisabled()

  // An edit in the text is the editor's document when the view closes.
  await toggle.click()
  await source.fill(markdown.replace('## 결론', '## 맺음말').replace('3억', '**3억**'))
  await toggle.click()
  await expect(editor(page).locator('h2')).toHaveText(['개요', '분석', '맺음말'])
  await expect(editor(page).locator('td strong')).toHaveText('3억')
  await toggle.click()
  await expect(source).toHaveValue(markdown.replace('## 결론', '## 맺음말').replace('3억', '**3억**'))
  await toggle.click()
  await save(page)

  const saved = state.writes[0].data.sections
  expect(saved.map((row) => [row.id, row.heading])).toEqual([['s1', '개요'], ['s2', '분석'], ['s3', '맺음말']])
  expect(saved[1].content).toContain('| 비용 | **3억** |')
  expect(state.unexpected).toEqual([])
})

test('a chat edit reloads the editor with the new version', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  await expect(editor(page).getByText('3억')).toBeVisible()
  await page.getByLabel('프롬프트 입력').fill('분석 절 비용을 2억으로 고쳐 줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(editor(page).getByText('2억')).toBeVisible({ timeout: 15_000 })
  await expect(editor(page).getByText('3억')).toHaveCount(0)
  await expect(editor(page).locator('h2')).toHaveText(['개요', '분석', '결론'])
  // The reloaded editor has nothing of its own to save.
  await expect(page.getByRole('button', { name: '저장', exact: true })).toBeDisabled()
  expect(state.writes).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('the ribbon reads like a word processor\'s and offers only Markdown', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  const ribbon = page.getByRole('tablist', { name: '보고서 메뉴' })
  await expect(ribbon.getByRole('tab')).toHaveText(['홈', '삽입', '레이아웃', '검토', '보기'])
  const groups = page.locator('[data-ribbon-look="word"] [data-ribbon-group]')
  for (const caption of ['실행 취소', '글자', '단락', '스타일', '편집']) {
    await expect(groups.locator('[data-ribbon-caption]', { hasText: caption }).first()).toBeVisible()
  }
  await expect(page.getByRole('radio', { name: /절 제목/ })).toBeVisible()
  // No formatting Markdown cannot hold.
  for (const gone of ['서체', '글자 크기', '글자색', '형광펜', '줄간격', '밑줄', '가운데 맞춤', '양쪽 맞춤', '선택한 셀 병합']) {
    await expect(page.getByLabel(gone, { exact: true })).toHaveCount(0)
  }
  await editor(page).getByText('첫 절 본문').click()
  await page.getByRole('button', { name: '굵게' }).click()
  await shot(page, 'editor-ribbon-1440')
  await page.getByRole('tab', { name: '삽입' }).click()
  for (const caption of ['표', '그림', '도식', '각주·출처', '구분']) {
    await expect(groups.locator('[data-ribbon-caption]', { hasText: caption }).first()).toBeVisible()
  }
  await shot(page, 'editor-insert-1440')
  await page.getByRole('tab', { name: '레이아웃' }).click()
  await expect(page.getByRole('button', { name: '페이지 설정' })).toBeVisible()
  expect(state.unexpected).toEqual([])
})

test('the header follows the document panels: one order, explicit save, the conflict notice', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  const header = panel(page).locator('header').first()
  const save = header.getByRole('button', { name: '저장', exact: true })
  await expect(save).toBeDisabled()
  await expect(header.getByRole('button', { name: '내려받기' })).toBeVisible()
  await expect(header.getByRole('button', { name: '이 아티팩트만 공유' })).toBeVisible()
  await expect(header.getByRole('button', { name: '더보기' })).toBeVisible()
  await expect(header.getByText('v1', { exact: true })).toBeVisible()
  await editor(page).getByText('첫 절 본문').click()
  await page.keyboard.press('End')
  await page.keyboard.type(' 덧붙임')
  await expect(header.getByText('저장 안 됨')).toBeVisible()
  await expect(save).toBeEnabled()
  // Someone else saved meanwhile: the save is refused, the notice offers both ways out.
  state.bump()
  await page.keyboard.press('Control+s')
  await expect(page.getByText('그사이 다른 판이 저장되어 저장하지 않았습니다.')).toBeVisible()
  await expect(page.getByRole('button', { name: '내 편집 내려받기' })).toBeVisible()
  await page.getByRole('button', { name: '최신 판 불러오기' }).click()
  await expect(editor(page).getByText('다른 사람이 고친 본문')).toBeVisible()
  await expect(header.getByText('저장 안 됨')).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})

test('a phone reads the report; the agent edits it', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await fixture(page, testInfo)
  await expect(panel(page).getByRole('heading', { name: '소제목' })).toBeVisible()
  await expect(page.getByTestId('report-read-only')).toBeVisible()
  await expect(panel(page).locator('header').getByRole('button', { name: '편집', exact: true })).toHaveCount(0)
  await shot(page, 'report-phone-390')
  expect(state.unexpected).toEqual([])
})

test('a chat turn over unsaved edits asks first, then saves and sends', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  await editor(page).getByText('담당:').click()
  await page.keyboard.press('End')
  await page.keyboard.type(' 확인')
  await page.getByLabel('프롬프트 입력').fill('분석 절 비용을 2억으로 고쳐 줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  const ask = page.getByRole('dialog', { name: '저장하지 않은 편집이 있습니다' })
  await expect(ask).toBeVisible()
  await ask.getByRole('button', { name: '저장하고 보내기' }).click()
  // saved first (v2), then the agent's version (v3) reloads the editor
  await expect(editor(page).getByText('2억')).toBeVisible({ timeout: 15_000 })
  await expect(editor(page).getByText('담당: [담당자] 확인')).toBeVisible()
  expect(state.writes).toHaveLength(1)
  expect(state.writes[0].data.sections[2].content).toBe('담당: [담당자] 확인')
  expect(state.unexpected).toEqual([])
})

test('a hand save writes the Markdown typed, never the closing 「끝.」 the page draws', async ({ page }, testInfo) => {
  // 개조식 with a closing mark: the page view adds 「끝.」 and its gap; the stored text has neither.
  const state = await fixture(page, testInfo, {
    titleBlock: { format: 'official', head: 'header', numbering: 'none', lists: 'gaejo', closing: true, fields: [] },
  })
  await enterEditor(page)
  await editor(page).getByText('담당:').click()
  await page.keyboard.press('End')
  await page.keyboard.type(' 완료')
  await save(page)
  const saved = state.writes[0].data.sections
  expect(saved[2].content).toBe('담당: [담당자] 완료')
  for (const row of saved) {
    expect(row.content).not.toContain('끝.')
    expect(row.content).not.toMatch(/\u00a0|&nbsp;/)
  }
  expect(state.unexpected).toEqual([])
})

test('leaving for a new conversation over unsaved edits asks first', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  await editor(page).getByText('담당:').click()
  await page.keyboard.press('End')
  await page.keyboard.type(' 확인')
  await page.getByRole('link', { name: '새 대화' }).click()
  const ask = page.getByRole('dialog', { name: '저장하지 않은 편집이 있습니다' })
  await expect(ask).toBeVisible()
  await ask.getByRole('button', { name: '취소' }).click()
  // stayed, edits kept
  await expect(editor(page).getByText('담당: [담당자] 확인')).toBeVisible()
  await page.getByRole('link', { name: '새 대화' }).click()
  await ask.getByRole('button', { name: '저장', exact: true }).click()
  await expect(page).not.toHaveURL(new RegExp(sessionId))
  expect(state.writes).toHaveLength(1)
  expect(state.writes[0].data.sections[2].content).toBe('담당: [담당자] 확인')
  expect(state.unexpected).toEqual([])
})
