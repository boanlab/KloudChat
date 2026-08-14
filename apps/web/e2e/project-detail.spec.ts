import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

const stamp = () => Math.random().toString(36).slice(2, 8)

/**
 * The project detail screen.
 *
 * Asserts the two things that make a project more than a folder: knowledge
 * files that precede every conversation inside it, and new work started from
 * within the project itself.
 */
test('프로젝트에 올린 지식 파일이 목록과 새로고침에 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const name = `지식 프로젝트 ${stamp()}`
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
})

test('프로젝트 안에서 새 작업을 시작하면 그 프로젝트에 속한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const name = `작업 프로젝트 ${stamp()}`
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
  const projectId = page.url().split('/').pop() as string

  await page.getByRole('button', { name: /이 프로젝트에서 새로 만들기/ }).click()
  await page.getByRole('menuitem', { name: '챗' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  // Belonging is what the shortcut is for: a session started here has to carry
  // the project, or the instructions and knowledge files never reach it.
  const sessionId = page.url().split('/').pop() as string
  const session = await page.evaluate(async (id) => {
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
    return r.ok ? await r.json() : null
  }, sessionId)
  expect(session?.projectId).toBe(projectId)
})
