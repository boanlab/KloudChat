import { expect, test, type Page, type TestInfo } from '@playwright/test'
import style from './fixtures/report-table-style.json' with { type: 'json' }

test.use({ serviceWorkers: 'block' })

const sessionId = '33333333333333333333333333333333'
const artifactId = '44444444444444444444444444444444'
const at = '2026-10-04T00:00:00.000Z'

const blocks = {
  cover: { format: 'term', label: '대학 과제 리포트', head: 'cover', numbering: 'decimal',
    fields: [['과목', '회로이론'], ['담당교수', ''], ['학과', ''], ['학번', ''], ['이름', ''], ['제출일', '2026-10-02']] },
  memo: { format: 'official', label: '공문·안내문', head: 'memo', numbering: 'official',
    fields: [['수신', '각 학과장'], ['참조', ''], ['발신', '교무처'], ['시행일', '']] },
  press: { format: 'press', label: '보도자료', head: 'press', numbering: 'none', subtitle: '부제 한 줄',
    fields: [['배포일', '2026-10-04'], ['보도 시점', ''], ['문의처', '홍보팀']] },
  paper: { format: 'paper', label: '학술 논문', head: 'paper', numbering: 'roman', abstract: '', keywords: ['검색', '요약'],
    fields: [['저자', '이연구'], ['소속', '단국대학교'], ['교신저자', '']] },
  header: { format: 'incident', label: '장애 보고서', head: 'header', numbering: 'decimal',
    fields: [['발생 일시', ''], ['영향 범위', '검색'], ['심각도', 'SEV2'], ['작성자', '']] },
} as const

async function fixture(page: Page, testInfo: TestInfo, titleBlock: unknown) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const unexpected: string[] = []
  const writes: { data: Record<string, unknown> }[] = []
  let artifact = {
    id: artifactId, title: '시험 문서', kind: 'report', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { kind: 'report', title: '시험 문서', sources: [], titleBlock,
      sections: [
        { id: 's1', heading: '서론', level: 1, status: 'done', content: '본문.\n\n### 배경\n\n내용.\n\n### 목적\n\n내용.' },
        { id: 's2', heading: '결론', level: 1, status: 'done', content: '마무리.' },
      ] },
  }
  const session = { id: sessionId, title: artifact.title, kind: 'report', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId, pinned: false, messages: [], messageCount: 0,
    made: null, createdAt: at, updatedAt: at }
  await page.context().routeWebSocket('**/*', (socket) => { unexpected.push('WebSocket'); socket.close() })
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'GET' && path === '/design-templates/doc-report/style') return route.fulfill({ json: style })
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
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [session] })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: [artifact] })
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return route.fulfill({ json: artifact })
    if (method === 'PATCH' && path === `/artifacts/${artifactId}`) {
      const patch = request.postDataJSON()
      writes.push(patch)
      artifact = { ...artifact, title: patch.title ?? artifact.title, data: patch.data, version: artifact.version + 1 }
      return route.fulfill({ json: artifact })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: { report: 1 }, total: 1 } })
    if (method === 'GET' && [`/sessions/${sessionId}/messages`, `/sessions/${sessionId}/jobs`, '/projects',
      '/skills', '/memory', '/agents', '/tools', '/templates', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/design-templates', '/prompt-templates', '/shares'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.abort('blockedbyclient')
  })
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  return { unexpected, writes }
}

async function enterEditor(page: Page) {
  await page.getByRole('tab', { name: '편집', exact: true }).click()
  await expect(page.locator('.ProseMirror').first()).toBeVisible()
}

for (const [head, block] of Object.entries(blocks)) {
  test(`the ${head} head is drawn with its fields and numbers`, async ({ page }, testInfo) => {
    const state = await fixture(page, testInfo, block)
    await enterEditor(page)
    const drawn = page.locator(`.report-page-shell header.tb.tb-${block.head}`)
    await expect(drawn).toBeVisible()
    for (const [label, value] of block.fields) {
      const input = drawn.getByRole('textbox', { name: label, exact: true })
      await expect(input).toHaveValue(value)
      if (!value) await expect(input).toHaveAttribute('placeholder', '(기입)')
    }
    const first = page.locator('.report-page-shell section').first().locator('h2').first()
    const expected = { decimal: '1.', roman: 'I.', official: '1.', korean: '1.', none: null }[block.numbering]
    if (expected) await expect(first).toHaveAttribute('data-num', expected)
    else await expect(first).not.toHaveAttribute('data-num', /./)
    const sub = page.locator('.report-page-shell .ProseMirror').locator('h2, h3, h4').first()
    const before = await sub.evaluate((node) => getComputedStyle(node, '::before').content)
    const want = { decimal: 'counter(tba)', roman: 'upper-alpha', official: 'hangul', korean: 'hangul', none: 'none' }[block.numbering]
    expect(before.startsWith(want) || before.includes(want)).toBeTruthy()
    await page.screenshot({ path: testInfo.outputPath(`${head}-edit.png`) })
    expect(state.unexpected).toEqual([])
  })
}

test('a head field is edited in place and saved with the document', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo, blocks.memo)
  await enterEditor(page)
  await page.getByRole('textbox', { name: '참조', exact: true }).fill('학생처장')
  const button = page.getByRole('button', { name: '저장', exact: true })
  await button.click()
  await expect(button).toBeHidden()
  expect(state.writes).toHaveLength(1)
  const saved = state.writes[0].data.titleBlock as { fields: [string, string][] }
  expect(saved.fields).toContainEqual(['참조', '학생처장'])
  expect(saved.fields).toContainEqual(['수신', '각 학과장'])
  expect(state.unexpected).toEqual([])
})

test('the web view and the page view show the cover', async ({ page }, testInfo) => {
  await fixture(page, testInfo, blocks.cover)
  const web = page.locator('article.doc-web')
  await expect(web.locator('header.tb-cover')).toBeVisible()
  await expect(web.locator('header.tb-cover .tb-blank').first()).toHaveText('(기입)')
  await expect(web.locator('section h2').first()).toHaveText('1. 서론')
  const sub = await web.locator('section :is(h2, h3, h4)').nth(1).evaluate((node) => getComputedStyle(node, '::before').content)
  expect(sub).toContain('counter(tba)')
  await page.screenshot({ path: testInfo.outputPath('cover-web.png') })
  await page.getByRole('button', { name: '페이지뷰', exact: true }).click()
  const cover = page.locator('.pagedjs_page').first().locator('header.tb-cover')
  await expect(cover).toBeVisible({ timeout: 20_000 })
  await expect(cover.locator('.tb-blank').first()).toHaveText('(기입)')
  await expect(page.locator('.pagedjs_page').first().locator('section')).toHaveCount(0)
  await expect(page.locator('.pagedjs_page h2[data-num="1."]').first()).toBeAttached()
  await expect(page.locator('.pagedjs_page :is(h2, h3, h4)[data-num="1.1"]').first()).toBeAttached()
  await page.screenshot({ path: testInfo.outputPath('cover-pages.png') })
})

test('a report without a head keeps its plain cover', async ({ page }, testInfo) => {
  await fixture(page, testInfo, undefined)
  await enterEditor(page)
  await expect(page.locator('header.tb')).toHaveCount(0)
  await expect(page.locator('.cover h1')).toHaveText('시험 문서')
  await expect(page.locator('h2[data-num]')).toHaveCount(0)
})
