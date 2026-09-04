import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

const stamp = () => Math.random().toString(36).slice(2, 8)

/** The session row as the server holds it. */
async function storedSession(page: Page, sessionId: string) {
  return page.evaluate(async (id) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = await login.json()
    const r = await fetch(`/api/sessions/${id}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    if (!r.ok) return null
    return (await r.json()) as { projectId: string; renderTemplateId: string | null }
  }, sessionId)
}

/** Creates a project and opens it; returns its id. */
async function createProject(page: Page, name: string) {
  await page.goto('/projects')
  await page.getByRole('button', { name: /새 프로젝트|프로젝트 만들기|만들기/ }).first().click()
  await page.getByRole('dialog').getByLabel(/이름/).first().fill(name)
  await page
    .getByRole('dialog')
    .getByRole('button', { name: /^만들기$|^생성$|^추가$|^저장$/ })
    .last()
    .click()
  await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 })
  await page.getByText(name).first().click()
  await expect(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 15_000 })
  return page.url().split('/').pop() as string
}

/** Project detail: knowledge files, work started from inside the project, and its default 서식. */
test('프로젝트에 올린 지식 파일이 목록과 새로고침에 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await createProject(page, `지식 프로젝트 ${stamp()}`)

  await page.getByRole('tab', { name: /^지식/ }).click()
  await expect(page.getByText('참고 파일이 없습니다')).toBeVisible()

  const filename = `연구노트-${stamp()}.md`
  await page.getByLabel('지식 파일 선택').setInputFiles({
    name: filename,
    mimeType: 'text/markdown',
    buffer: Buffer.from('# 실험 조건\n\n표본 수는 240, 반복은 3회.\n'),
  })

  // A file that lands with 0 tokens was not read.
  const row = page.locator('div').filter({ hasText: filename }).last()
  await expect(row).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/[1-9][0-9,]*\s*토큰/).first()).toBeVisible()

  // Survives a reload.
  await page.reload()
  await page.getByRole('tab', { name: /^지식/ }).click()
  await expect(page.getByText(filename)).toBeVisible({ timeout: 20_000 })

  // The name downloads the file; the bytes are what was uploaded.
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    // Exact: the row's delete button is labelled "{name} 삭제".
    page.getByRole('button', { name: filename, exact: true }).click(),
  ])
  expect(download.suggestedFilename()).toBe(filename)
  const saved = await download.path()
  expect(readFileSync(saved, 'utf8')).toContain('표본 수는 240')
})

test('웹페이지를 프로젝트 자료로 보관하고 읽힌 내용을 확인한다', async ({ page }) => {
  await signIn(page)
  const projectId = await createProject(page, `웹 자료 프로젝트 ${stamp()}`)
  const url = 'https://research.example.org/market-2026'

  await page.route(`**/api/projects/${projectId}/knowledge/url`, async (route) => {
    expect(route.request().postDataJSON()).toEqual({ url })
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'web-snapshot-1',
        name: 'research.example.org-market-2026',
        size: 4200,
        mime: 'text/markdown',
        tokens: 930,
        projectId,
        sessionId: null,
        sourceUrl: url,
        preview: '2026년 시장 규모는 조사 표본 1,240건을 기준으로 산정했다.',
        error: null,
        indexed: false,
        createdAt: new Date().toISOString(),
      }),
    })
  })

  await page.getByRole('tab', { name: /^지식/ }).click()
  await page.getByRole('button', { name: '웹 자료' }).click()
  const modal = page.getByRole('dialog', { name: '웹 자료 추가' })
  await modal.getByLabel('웹페이지 주소').fill(url)
  await modal.getByRole('button', { name: '읽어서 보관' }).click()

  const row = page.locator('[data-knowledge="web-snapshot-1"]')
  await expect(row).toContainText('웹페이지 스냅샷')
  await expect(row).toContainText('930 토큰')
  await row.getByRole('button', { name: '읽은 내용 확인' }).click()
  await expect(row.getByTestId('knowledge-preview')).toContainText('조사 표본 1,240건')
  await expect(row.getByRole('link', { name: /원문 열기/ })).toHaveAttribute('href', url)
})

test('프로젝트 안에서 새 작업을 시작하면 그 프로젝트에 속한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const projectId = await createProject(page, `작업 프로젝트 ${stamp()}`)

  await page.getByRole('button', { name: /이 프로젝트에서 새로 만들기/ }).click()
  await page.getByRole('menuitem', { name: '챗' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  // Without the project id the instructions and knowledge files never reach the session.
  const session = await storedSession(page, page.url().split('/').pop() as string)
  expect(session?.projectId).toBe(projectId)
})

/** A project's default 서식 opens new sessions in that shape; a session may still clear it. No turn is sent. */
test('프로젝트에 서식을 정하면 그 안에서 시작한 작업이 그 서식으로 열린다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const projectId = await createProject(page, `서식 프로젝트 ${stamp()}`)

  // The picker writes optimistically; wait for the PATCH.
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/projects/${projectId}`) &&
      r.request().method() === 'PATCH' &&
      r.status() === 200,
    { timeout: 20_000 },
  )
  const picker = page.getByLabel('보고서 서식')
  await expect(picker).toBeVisible({ timeout: 20_000 })
  await picker.selectOption({ label: '안내문·공지' })
  const stored = (await (await saved).json()) as { renderTemplates: Record<string, string> }
  expect(stored.renderTemplates).toEqual({ report: 'doc-notice' })

  // The map is sent whole: setting one key must not clear the others.
  await page.reload()
  await expect(page.getByLabel('보고서 서식')).toHaveValue('doc-notice', { timeout: 20_000 })
  await expect(page.getByLabel('슬라이드 서식')).toHaveValue('')

  await page.getByRole('button', { name: /이 프로젝트에서 새로 만들기/ }).click()
  await page.getByRole('menuitem', { name: '보고서' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  const sessionId = page.url().split('/').pop() as string

  const started = await storedSession(page, sessionId)
  expect(started?.renderTemplateId).toBe('doc-notice')
  await expect(page.getByText('안내문·공지').first()).toBeVisible({ timeout: 20_000 })

  // Clearing the chip is this conversation's decision; the project keeps its default.
  await page.getByRole('button', { name: /안내문·공지.*해제/ }).click()
  await expect
    .poll(async () => (await storedSession(page, sessionId))?.renderTemplateId, {
      timeout: 20_000,
    })
    .toBeNull()

  await page.goto(`/projects/${projectId}`)
  await expect(page.getByLabel('보고서 서식')).toHaveValue('doc-notice', { timeout: 20_000 })
})
