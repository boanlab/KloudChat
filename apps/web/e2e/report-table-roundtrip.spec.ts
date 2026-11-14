import { expect, test, type Page, type TestInfo } from '@playwright/test'
import style from './fixtures/report-table-style.json' with { type: 'json' }
import { shellRead } from './shell-mocks'

test.use({ serviceWorkers: 'block' })

const sessionId = '11111111111111111111111111111111'
const artifactId = '22222222222222222222222222222222'
const at = '2026-09-12T00:00:00.000Z'
// A GFM table: the web report is Markdown, so a table has a header row and no merged cells.
const original = '| Item | Value | Owner |\n| --- | --- | --- |\n| Alpha | 10 | A |\n| Beta | 20 | B |'

async function fixture(page: Page, testInfo: TestInfo) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  expect(['localhost', '127.0.0.1']).toContain(new URL(origin).hostname)
  const unexpected: string[] = []
  const writes: unknown[] = []
  let artifact = {
    id: artifactId, title: 'Table roundtrip', kind: 'report', version: 1, partial: false,
    sessionId, projectId: null, createdAt: at, updatedAt: at,
    data: { kind: 'report', title: 'Table roundtrip', sources: [],
      sections: [{ id: 'table-section', heading: 'Comparison', content: original, level: 1, status: 'done' }] },
  }
  const session = { id: sessionId, title: artifact.title, kind: 'report', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId, pinned: false, messages: [], messageCount: 0,
    made: null, createdAt: at, updatedAt: at }
  await page.context().routeWebSocket('**/*', (socket) => { unexpected.push('WebSocket'); socket.close() })
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
    if (method === 'GET' && path === '/sessions') return route.fulfill({ json: [session] })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'GET' && path === '/artifacts') return route.fulfill({ json: [artifact] })
    if (method === 'GET' && path === `/artifacts/${artifactId}`) return route.fulfill({ json: artifact })
    if (method === 'PATCH' && path === `/artifacts/${artifactId}`) {
      const patch = request.postDataJSON()
      expect(patch.expectedVersion).toBe(artifact.version)
      writes.push(patch)
      artifact = { ...artifact, title: patch.title ?? artifact.title, data: patch.data, version: artifact.version + 1 }
      return route.fulfill({ json: artifact })
    }
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: { report: 1 }, total: 1 } })
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && [`/sessions/${sessionId}/messages`, `/sessions/${sessionId}/jobs`, '/projects',
      '/skills', '/memory', '/agents', '/tools', '/connectors', '/connectors/catalog', '/jobs',
      '/designs', '/design-templates', '/shares'].includes(path)) return route.fulfill({ json: [] })
    unexpected.push(`${method} ${path}`)
    return route.abort('blockedbyclient')
  })
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  return { unexpected, writes, stored: () => structuredClone(artifact) }
}

async function enterEditor(page: Page) {
  // 「편집」 in the panel header opens the page editor and the editing tabs.
  await page.locator('[data-panel="artifact"] header').getByRole('button', { name: '편집', exact: true }).click()
  await expect(page.locator('.ProseMirror').first()).toBeVisible()
}

async function save(page: Page) {
  const button = page.getByRole('button', { name: '저장', exact: true })
  await button.click()
  await expect(button).toBeDisabled()
}

const tableOf = (state: { stored: () => { data: { sections: { content: string }[] } } }) =>
  state.stored().data.sections[0].content

test('a row added in the editor is saved as a Markdown table row', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  const editor = page.locator('.ProseMirror').first()
  await expect(editor.locator('th')).toHaveCount(3)
  await editor.locator('td').filter({ hasText: 'Beta' }).click()
  await page.getByRole('tab', { name: '삽입' }).click()
  await page.getByRole('button', { name: '아래에 행 추가', exact: true }).click()
  await expect(editor.locator('tr')).toHaveCount(4)
  await editor.locator('tr').nth(3).locator('td').first().click()
  await page.keyboard.type('Gamma')
  await save(page)
  expect(tableOf(state)).toBe(`${original}\n| Gamma |  |  |`)
  await page.reload()
  await enterEditor(page)
  await expect(page.locator('.ProseMirror').first().locator('tr')).toHaveCount(4)
  expect(state.writes).toHaveLength(1)
  expect(state.unexpected).toEqual([])
})

test('opening and focusing a table does not offer a phantom save', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  await page.locator('.ProseMirror').first().locator('th').first().click()
  await expect(page.getByRole('button', { name: '저장', exact: true })).toBeDisabled()
  expect(state.writes).toEqual([])
  expect(state.unexpected).toEqual([])
})

for (const redo of [false, true]) {
  test(`row undo${redo ? ' and redo' : ''} survives save and reload`, async ({ page }, testInfo) => {
    const state = await fixture(page, testInfo)
    await enterEditor(page)
    const editor = page.locator('.ProseMirror').first()
    await editor.locator('td').filter({ hasText: 'Alpha' }).click()
    await page.getByRole('tab', { name: '삽입' }).click()
    await page.getByRole('button', { name: '현재 행 삭제', exact: true }).click()
    await expect(editor.locator('tr')).toHaveCount(2)
    await page.getByRole('tab', { name: '홈' }).click()
    await page.getByRole('button', { name: '실행 취소', exact: true }).click()
    await expect(editor.locator('tr')).toHaveCount(3)
    if (redo) {
      await page.getByRole('button', { name: '다시 실행', exact: true }).click()
      await expect(editor.locator('tr')).toHaveCount(2)
      await save(page)
      expect(tableOf(state)).toBe('| Item | Value | Owner |\n| --- | --- | --- |\n| Beta | 20 | B |')
    } else {
      // Back where it started: nothing to save.
      await expect(page.getByRole('button', { name: '저장', exact: true })).toBeDisabled()
    }
    await page.reload()
    await enterEditor(page)
    await expect(page.locator('.ProseMirror').first().locator('tr')).toHaveCount(redo ? 2 : 3)
    expect(state.writes).toHaveLength(redo ? 1 : 0)
    expect(state.unexpected).toEqual([])
  })
}

test('a table offers no merged cells: Markdown has none', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  await page.locator('.ProseMirror').first().locator('td').first().click()
  await page.getByRole('tab', { name: '삽입' }).click()
  await expect(page.getByRole('button', { name: '아래에 행 추가', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '선택한 셀 병합' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '셀 나누기' })).toHaveCount(0)
  expect(state.unexpected).toEqual([])
})

test('restoring the original title survives save and reload', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  const line = page.locator('h1[contenteditable="true"]').first()
  await line.fill('Changed text')
  await line.press('Enter')
  await line.fill('Table roundtrip')
  await line.press('Enter')
  await expect(line).toHaveText('Table roundtrip')
  await save(page)
  await page.reload()
  await enterEditor(page)
  await expect(line).toHaveText('Table roundtrip')
  expect(state.writes).toHaveLength(1)
  expect(state.unexpected).toEqual([])
})

test('empty or cancelled title edits do not overwrite the accepted value', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  const line = page.locator('h1[contenteditable="true"]').first()
  await line.fill('Accepted text')
  await line.press('Enter')
  await line.fill('Cancelled text')
  await line.press('Escape')
  await expect(line).toHaveText('Accepted text')
  await line.fill('   ')
  await line.press('Enter')
  await save(page)
  await page.reload()
  await enterEditor(page)
  await expect(line).toHaveText('Accepted text')
  expect(state.writes).toHaveLength(1)
  expect(state.unexpected).toEqual([])
})

test('a section heading renamed in the editor is saved with its id', async ({ page }, testInfo) => {
  const state = await fixture(page, testInfo)
  await enterEditor(page)
  const heading = page.locator('.ProseMirror').first().getByRole('heading', { name: 'Comparison' })
  await heading.click()
  await page.keyboard.press('End')
  await page.keyboard.type(' table')
  await save(page)
  const saved = state.stored().data.sections[0] as { id: string; heading: string; content: string }
  expect([saved.id, saved.heading, saved.content]).toEqual(['table-section', 'Comparison table', original])
  expect(state.unexpected).toEqual([])
})
