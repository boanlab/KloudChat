import { expect, test, type Page, type Route } from '@playwright/test'
import { at, hubFixture, installedConnectors } from './customize-fixture'

/**
 * Saves and deletes the server refuses, against fixture data: the screen goes back to what the
 * server has and says so, instead of showing a change that never happened. A project's file
 * waits out the undo window before it is deleted; a shared link that failed to load is told
 * apart from one that was revoked.
 */

const PROJECT = 'p1111111111111111111111111111111'
const FILE = 'f1111111111111111111111111111111'

function fileRow(id: string, name: string) {
  return { id, name, size: 1200, mime: 'text/markdown', tokens: 300, projectId: PROJECT, sessionId: null, preview: '',
    error: null, createdAt: at }
}

function projectRow(extra: Record<string, unknown> = {}) {
  return { id: PROJECT, name: '학과 행정', description: '', emoji: '📁', instructions: '공문체로 쓴다.', skillIds: [],
    designSystemId: null, renderTemplates: {}, files: [fileRow(FILE, '학사일정.md')], sessionIds: [], createdAt: at,
    updatedAt: at, ...extra }
}

/** The project on top of the hub fixture; `deletes` and `patches` record the writes. */
async function projectRoutes(page: Page, { failDelete = false, failPatch = false } = {}) {
  const deletes: string[] = []
  const patches: unknown[] = []
  await page.route('**/api/projects', (route) => route.fulfill({ json: [projectRow()] }))
  await page.route(`**/api/projects/${PROJECT}`, (route: Route) => {
    if (route.request().method() === 'PATCH') {
      patches.push(route.request().postDataJSON())
      return failPatch
        ? route.fulfill({ status: 500, json: { detail: 'Internal Server Error' } })
        : route.fulfill({ json: projectRow(route.request().postDataJSON() as Record<string, unknown>) })
    }
    return route.fulfill({ json: projectRow() })
  })
  await page.route(`**/api/files/${FILE}`, (route) => {
    deletes.push(route.request().method())
    return failDelete ? route.fulfill({ status: 500, json: { detail: 'Internal Server Error' } }) : route.fulfill({ status: 204 })
  })
  return { deletes, patches }
}

test.use({ serviceWorkers: 'block' })

test('a project file is held for undo before it is deleted, and comes back if the delete fails', async ({ page }, testInfo) => {
  test.setTimeout(60_000)
  await hubFixture(page, testInfo)
  const { deletes } = await projectRoutes(page, { failDelete: true })
  await page.goto(`/projects/${PROJECT}`)
  await page.getByRole('tab', { name: /^지식/ }).click({ timeout: 15_000 })
  const remove = page.getByRole('button', { name: '학사일정.md 삭제' })

  await remove.click()
  await expect(remove).toHaveCount(0)
  await expect(page.getByText('학사일정.md 삭제됨')).toBeVisible()
  await page.getByRole('button', { name: '실행 취소' }).click()
  await expect(remove).toBeVisible()
  await page.waitForTimeout(6_500)
  expect(deletes).toEqual([])

  // Sent after the undo window; the server refuses and the file is back with the reason.
  await remove.click()
  await expect(remove).toHaveCount(0)
  await expect(page.getByRole('alert')).toContainText('학사일정.md을(를) 지우지 못했습니다.', { timeout: 10_000 })
  await expect(remove).toBeVisible()
  expect(deletes).toEqual(['DELETE'])
})

test('project instructions that did not save stay unsaved with the reason', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo)
  const { patches } = await projectRoutes(page, { failPatch: true })
  await page.goto(`/projects/${PROJECT}`)
  const box = page.getByRole('textbox', { name: '프로젝트 지침' })
  await expect(box).toHaveValue('공문체로 쓴다.', { timeout: 15_000 })
  await box.fill('공문체로 쓰고 날짜는 2026. 3. 2. 꼴로 쓴다.')
  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('저장하지 못했습니다.')
  // Still marked unsaved, with the text as typed, so it can be sent again.
  await expect(page.getByRole('button', { name: '저장', exact: true })).toBeVisible()
  await expect(box).toHaveValue('공문체로 쓰고 날짜는 2026. 3. 2. 꼴로 쓴다.')
  expect(patches).toEqual([{ instructions: '공문체로 쓰고 날짜는 2026. 3. 2. 꼴로 쓴다.' }])
})

test('a connector switch the server refused goes back and says why', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo, { connectors: installedConnectors })
  const sent: unknown[] = []
  await page.route('**/api/connectors/n2', (route) => {
    sent.push(route.request().postDataJSON())
    return route.fulfill({ status: 500, json: { detail: 'Internal Server Error' } })
  })
  await page.goto('/customize?tab=connectors')
  const toggle = page.getByRole('switch', { name: '사내 위키 사용' })
  await expect(toggle).toHaveAttribute('aria-checked', 'true', { timeout: 15_000 })
  await toggle.click()
  await expect(page.getByRole('alert')).toContainText('커넥터를 끄지 못했습니다.')
  await expect(toggle).toHaveAttribute('aria-checked', 'true')
  expect(sent).toEqual([{ enabled: false }])
})

test('the connector list is searched by name', async ({ page }, testInfo) => {
  await hubFixture(page, testInfo, { connectors: installedConnectors })
  await page.goto('/customize?tab=connectors')
  const list = page.getByTestId('connector-list')
  await expect(list.locator('[data-connector]')).toHaveCount(2, { timeout: 15_000 })
  await page.getByRole('searchbox', { name: '커넥터 검색' }).fill('위키')
  await expect(list.locator('[data-connector]')).toHaveCount(1)
  await expect(list.locator('[data-connector="wiki"]')).toBeVisible()
  await page.getByRole('searchbox', { name: '커넥터 검색' }).fill('없는 이름')
  await expect(page.getByTestId('no-match')).toBeVisible()
})

/** A shared link opened without an account: `answers` are the server's replies, in order. */
async function sharedRoutes(page: Page, answers: { status: number; json: unknown }[]) {
  const origin = new URL(String(test.info().project.use.baseURL)).origin
  await page.context().route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    if (path === '/auth/refresh') return route.fulfill({ status: 401, json: { detail: 'not_signed_in' } })
    if (path === '/auth/config') return route.fulfill({ json: { brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false, signupMode: 'open' } })
    if (path === '/shared/tok123') return route.fulfill(answers.length > 1 ? answers.shift()! : answers[0])
    return route.fulfill({ json: route.request().method() === 'GET' ? [] : {} })
  })
}

const sharedConversation = {
  kind: 'session', title: '공유된 대화', sessionKind: 'chat', startedWith: null, artifact: null,
  messages: [
    { id: 'm1', role: 'user', content: '장학금 신청 기간 알려 줘', attachments: [], createdAt: at },
    { id: 'm2', role: 'assistant', content: '신청은 3월 2일까지입니다.', attachments: [], createdAt: at },
  ],
}

test('a shared link the server failed to open says so and opens on 다시 시도', async ({ page }) => {
  await sharedRoutes(page, [{ status: 502, json: { detail: 'Bad Gateway' } }, { status: 200, json: sharedConversation }])
  await page.goto('/share/tok123')
  await expect(page.getByRole('heading', { name: '지금은 열지 못했습니다' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('shared-error')).not.toContainText('철회')
  await page.getByRole('button', { name: '다시 시도' }).click()
  await expect(page.getByText('신청은 3월 2일까지입니다.')).toBeVisible()
})

test('a revoked shared link reads as one, with nothing to retry', async ({ page }) => {
  await sharedRoutes(page, [{ status: 404, json: { detail: 'not_found' } }])
  await page.goto('/share/tok123')
  await expect(page.getByRole('heading', { name: '열 수 없는 링크입니다' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('shared-error')).toContainText('링크가 철회되었거나 주소가 올바르지 않습니다.')
  await expect(page.getByRole('button', { name: '다시 시도' })).toHaveCount(0)
})
