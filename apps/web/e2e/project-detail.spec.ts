import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

const stamp = () => Math.random().toString(36).slice(2, 8)

/**
 * The session as the server kept it.
 *
 * Read over the API rather than off the screen: what these tests are about is
 * the row, and a chip in the composer can be right while the column is empty.
 */
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

/** The create dialogue, which every test here starts with. */
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

/**
 * The project detail screen.
 *
 * Asserts the three things that make a project more than a folder: knowledge
 * files that precede every conversation inside it, new work started from
 * within the project itself, and the shape that work comes out in.
 */
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

  // The row carries what the file is worth in context, which is the number the
  // screen exists to show — a file that lands with 0 tokens was not read.
  const row = page.locator('div').filter({ hasText: filename }).last()
  await expect(row).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/[1-9][0-9,]*\s*토큰/).first()).toBeVisible()

  // Survives a reload, i.e. the server has it and the workspace snapshot did
  // not overwrite it on the way back.
  await page.reload()
  await page.getByRole('tab', { name: /^지식/ }).click()
  await expect(page.getByText(filename)).toBeVisible({ timeout: 20_000 })

  // The name gives the file back. Reading the bytes and not just the download
  // event is the point: the row is a claim about what was uploaded, and this is
  // the only way to check it short of deleting the file and uploading it again.
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    // Exact: the row's delete button is labelled "{name} 삭제".
    page.getByRole('button', { name: filename, exact: true }).click(),
  ])
  expect(download.suggestedFilename()).toBe(filename)
  const saved = await download.path()
  expect(readFileSync(saved, 'utf8')).toContain('표본 수는 240')
})

test('프로젝트 안에서 새 작업을 시작하면 그 프로젝트에 속한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const projectId = await createProject(page, `작업 프로젝트 ${stamp()}`)

  await page.getByRole('button', { name: /이 프로젝트에서 새로 만들기/ }).click()
  await page.getByRole('menuitem', { name: '챗' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  // Belonging is what the shortcut is for: a session started here has to carry
  // the project, or the instructions and knowledge files never reach it.
  const session = await storedSession(page, page.url().split('/').pop() as string)
  expect(session?.projectId).toBe(projectId)
})

/**
 * A format is a property of the project, not of every conversation in it.
 *
 * Nothing here sends a turn: what the finding was about is which shape a new
 * session opens in, and that is settled before the first message — asking a
 * model to write a notice to prove it would cost minutes and credits for an
 * assertion about a column.
 */
test('프로젝트에 서식을 정하면 그 안에서 시작한 작업이 그 서식으로 열린다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const projectId = await createProject(page, `서식 프로젝트 ${stamp()}`)

  // The picker writes optimistically, so waiting on the PATCH is what makes
  // the reload below a test of the column rather than a race with the request.
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

  // Survives a reload, and the surface next to it was left alone — the map is
  // sent whole, so this is the assertion that says setting one key is not
  // clearing the others.
  await page.reload()
  await expect(page.getByLabel('보고서 서식')).toHaveValue('doc-notice', { timeout: 20_000 })
  await expect(page.getByLabel('슬라이드 서식')).toHaveValue('')

  // ── the finding ──────────────────────────────────────────────────────
  await page.getByRole('button', { name: /이 프로젝트에서 새로 만들기/ }).click()
  await page.getByRole('menuitem', { name: '보고서' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  const sessionId = page.url().split('/').pop() as string

  const started = await storedSession(page, sessionId)
  expect(started?.renderTemplateId).toBe('doc-notice')
  // And the composer says so, which is the half a person can see.
  await expect(page.getByText('안내문·공지').first()).toBeVisible({ timeout: 20_000 })

  // ── the conversation still decides ───────────────────────────────────
  // Clearing the chip is this conversation's own decision. The project keeps
  // its default, or "always the notice form" would only hold until somebody
  // changed their mind once.
  await page.getByRole('button', { name: /안내문·공지.*해제/ }).click()
  await expect
    .poll(async () => (await storedSession(page, sessionId))?.renderTemplateId, {
      timeout: 20_000,
    })
    .toBeNull()

  await page.goto(`/projects/${projectId}`)
  await expect(page.getByLabel('보고서 서식')).toHaveValue('doc-notice', { timeout: 20_000 })
})
