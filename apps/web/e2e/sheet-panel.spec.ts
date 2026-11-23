import { expect, test, type Page } from '@playwright/test'
import { shellRead } from './shell-mocks'

/** The sheet panel beside a chat with the real Univer grid, under the app's CSP; API mocked. */

test.use({ serviceWorkers: 'block' })

const sessionId = '77777777777777777777777777777777'
const artifactId = '88888888888888888888888888888888'
const at = '2026-11-23T00:00:00.000Z'

const model = {
  version: 1, title: '매출', kept: [], truncated: false,
  sheets: [{
    name: '매출현황', rows: 3, cols: 2, merges: [], colWidths: {}, rowHeights: {},
    cells: [
      { r: 0, c: 0, v: '지점' }, { r: 0, c: 1, v: '1월' },
      { r: 1, c: 0, v: '서울' }, { r: 1, c: 1, v: 10 },
      { r: 2, c: 0, v: '합계' }, { r: 2, c: 1, v: 10, f: 'SUM(B2:B2)' },
    ],
  }],
}

async function fixture(page: Page, options: { conflict?: boolean } = {}) {
  const patches: { expectedVersion: number; ops: Record<string, unknown>[] }[] = []
  const unexpected: string[] = []
  const csp: string[] = []
  page.on('console', (message) => {
    if (/Content Security Policy/i.test(message.text())) csp.push(message.text())
  })
  let version = 1
  const artifact = () => ({
    id: artifactId, title: '매출 시트', kind: 'xlsx', version, partial: false, sessionId,
    projectId: null, createdAt: at, updatedAt: at,
    data: { format: 'xlsx', size: 4000, sheets: ['매출현황'], text: '지점 서울' },
  })
  const session = {
    id: sessionId, title: '매출 시트', kind: 'sheet', model: 'fixture/base', routingMode: 'manual',
    projectId: null, agentId: null, artifactId, pinned: false, messages: [], messageCount: 0,
    made: null, createdAt: at, updatedAt: at,
  }
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api/, '')
    const method = request.method()
    const json = (value: unknown) => route.fulfill({ json: value })
    if (path === '/auth/refresh') return json({
      accessToken: 'fixture-only', expiresIn: 3600,
      user: {
        id: 'fixture-user', email: 'fixture@example.test', name: 'Sheet', role: 'user',
        status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267',
        allowedModels: [], createdAt: at,
        preferences: { autoMemory: false, showUsage: false, streamResponses: true },
      },
    })
    if (path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'sheet'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    })
    if (path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'fixture/base', autoRouting: { enabled: false, available: false },
      models: [{
        id: 'fixture/base', label: 'Fixture', name: 'Fixture', vendor: 'Fixture', provider: 'fixture',
        kinds: ['chat'], modality: 'chat', dataBoundary: 'external', creditCost: 1, inputCreditCost: 1,
        supportsTools: true, contextWindow: 64000,
      }],
    })
    if (path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (path === '/sessions' && method === 'GET') return json([session])
    if (path === `/sessions/${sessionId}`) return json(session)
    if (path === `/sessions/${sessionId}/messages`) return json([])
    if (path === '/artifacts' && method === 'GET') return json([artifact()])
    if (path === `/artifacts/${artifactId}`) return json(artifact())
    if (path === `/artifacts/${artifactId}/versions`) return json([])
    if (path === `/artifacts/${artifactId}/xlsx/model`) return json({ ...model, version })
    if (path === `/artifacts/${artifactId}/xlsx/cells` && method === 'PATCH') {
      const body = request.postDataJSON()
      patches.push(body)
      if (options.conflict || body.expectedVersion !== version) {
        return route.fulfill({ status: 409, json: { detail: 'version_conflict' } })
      }
      version += 1
      return json(artifact())
    }
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET' && [`/sessions/${sessionId}/jobs`, '/projects', '/skills', '/memory',
      '/agents', '/tools', '/connectors', '/connectors/store', '/jobs', '/designs',
      '/design-templates', '/shares'].includes(path)) return json([])
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'not_found' } })
  })
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  return { patches, unexpected, csp }
}

const panel = (page: Page) => page.locator('[data-xlsx-panel]')
const header = (page: Page) => panel(page).locator('header')
const canvas = (page: Page) => page.locator('[data-sheet-editor] canvas').first()

/** Types into a cell of the grid at a screen point, as a person does. */
async function typeAt(page: Page, x: number, y: number, text: string) {
  await page.mouse.dblclick(x, y)
  await page.keyboard.insertText(text)
  await page.keyboard.press('Enter')
}

test('a sheet opens in the grid, a hand edit saves as cell changes on the version read', async ({ page }) => {
  const state = await fixture(page)
  await expect(canvas(page)).toBeVisible({ timeout: 30_000 })
  await expect(header(page).getByText('v1', { exact: true })).toBeVisible()
  await expect(header(page).getByRole('button', { name: '저장' })).toBeDisabled()
  // the grid's toolbar, in Korean
  await expect(panel(page).getByText('시작', { exact: true }).first()).toBeVisible()

  const box = (await canvas(page).boundingBox())!
  await typeAt(page, box.x + 60, box.y + 24 * 5, '대구')
  await expect(header(page).getByText('저장 안 됨')).toBeVisible()
  await header(page).getByRole('button', { name: '저장' }).click()
  await expect(header(page).getByText('v2', { exact: true })).toBeVisible()
  await expect(header(page).getByText('저장 안 됨')).toBeHidden()
  expect(state.patches).toHaveLength(1)
  expect(state.patches[0].expectedVersion).toBe(1)
  expect(state.patches[0].ops).toContainEqual(expect.objectContaining({ sheet: '매출현황', v: '대구' }))
  expect(state.unexpected).toEqual([])
  expect(state.csp).toEqual([])
})

test('a save that lost to a newer version says so, as the other editors do', async ({ page }) => {
  await fixture(page, { conflict: true })
  await expect(canvas(page)).toBeVisible({ timeout: 30_000 })
  const box = (await canvas(page).boundingBox())!
  await typeAt(page, box.x + 60, box.y + 24 * 5, '늦은 편집')
  await header(page).getByRole('button', { name: '저장' }).click()
  const notice = panel(page).getByRole('status')
  await expect(notice).toContainText('그사이 다른 판이 저장되어 저장하지 않았습니다.')
  await expect(notice.getByRole('button', { name: '최신 판 불러오기' })).toBeVisible()
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 } })
  test('the sheet is read only: no save, no toolbar, and the note says so', async ({ page }) => {
    await fixture(page)
    await expect(canvas(page)).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('office-read-only')).toBeVisible()
    await expect(header(page).getByRole('button', { name: '저장' })).toHaveCount(0)
    await expect(panel(page).getByText('시작', { exact: true })).toHaveCount(0)
  })
})
