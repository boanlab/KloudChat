/** Workspace screens against a real backend: projects, skills, memories, agents, connectors, attachments, web search. */

import { expect, test } from '@playwright/test'
import { answerText, pickToolModel, signIn } from './helpers'

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

/** Unique per run. */
const stamp = () => Math.random().toString(36).slice(2, 8)

/** Sends from `/new/chat` and waits for the session to be created. */
async function askFromNew(page: import('@playwright/test').Page, prompt: string) {
  await page.getByLabel('프롬프트 입력').fill(prompt)
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().endsWith('/api/sessions') && r.request().method() === 'POST',
    ),
    page.getByLabel('프롬프트 입력').press('Enter'),
  ])
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
}

/** Deletes a row and waits for the DELETE, which is held for the undo window. */
async function removeNamed(page: import('@playwright/test').Page, label: string) {
  await page.getByRole('button', { name: `${label} 삭제` }).first().click()
  const dialog = page.getByRole('dialog')
  const sent = page
    .waitForResponse((r) => r.request().method() === 'DELETE', { timeout: 20_000 })
    .catch(() => null)
  if (await dialog.isVisible().catch(() => false)) {
    await dialog.getByRole('button', { name: /^삭제$/ }).last().click().catch(() => {})
  }
  await sent
  await expect(page.getByText(label)).toHaveCount(0, { timeout: 15_000 })
}

test('프로젝트를 만들고 지침을 저장하면 새로고침 후에도 남는다', async ({ page }) => {
  const name = `프로젝트 ${stamp()}`
  await page.goto('/projects')
  await page.getByRole('button', { name: /새 프로젝트|프로젝트 만들기|만들기/ }).first().click()
  await page.getByRole('dialog').getByLabel(/이름/).first().fill(name)
  const instructions = page.getByRole('dialog').getByLabel(/지침/).first()
  if (await instructions.isVisible().catch(() => false)) {
    await instructions.fill('모든 수치에 단위를 붙인다.')
  }
  // Scoped to the dialog.
  await page.getByRole('dialog').getByRole('button', { name: /^만들기$|^생성$|^추가$|^저장$/ }).last().click()

  await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 })
  await page.reload()
  await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 })
})

test('스킬을 만들면 목록과 새로고침에 남는다', async ({ page }) => {
  const name = `스킬 ${stamp()}`
  await page.goto('/skills')
  await page.getByRole('button', { name: /스킬 (만들기|추가)|새 스킬|만들기/ }).first().click()
  await page.getByRole('dialog').getByLabel(/이름/).first().fill(name)
  await page.getByRole('dialog').getByRole('button', { name: /^저장$|^만들기$|^추가$/ }).last().click()

  await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 })
  await page.reload()
  await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 })
  await removeNamed(page, name)
})

test('메모리를 만들면 새로고침 후에도 남는다', async ({ page }) => {
  const name = `메모리 ${stamp()}`
  await page.goto('/memory')
  await page.getByRole('button', { name: /메모리 (추가|만들기)|새 메모리|추가/ }).first().click()
  await page.getByRole('dialog').getByLabel(/이름/).first().fill(name)
  const bodyField = page.getByRole('dialog').getByLabel(/내용|본문/).first()
  if (await bodyField.isVisible().catch(() => false)) await bodyField.fill('테스트 사실입니다.')
  await page.getByRole('dialog').getByRole('button', { name: /^저장$|^추가$|^만들기$/ }).last().click()

  await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 })
  await page.reload()
  await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 })
  // A leftover memory is `global` and joins every later turn.
  await removeNamed(page, name)
})

test('에이전트를 만들면 새로고침 후에도 남는다', async ({ page }) => {
  const name = `에이전트 ${stamp()}`
  await page.goto('/agents')
  await page.getByRole('button', { name: /에이전트 (만들기|추가)|새 에이전트|만들기/ }).first().click()
  await page.getByRole('dialog').getByLabel(/이름/).first().fill(name)
  const prompt = page.getByLabel(/시스템 프롬프트|프롬프트|지침/).first()
  if (await prompt.isVisible().catch(() => false)) await prompt.fill('테스트 에이전트입니다.')
  await page.getByRole('dialog').getByRole('button', { name: /^저장$|^만들기$|^추가$/ }).last().click()

  await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 })
  await page.reload()
  await expect(page.getByText(name)).toBeVisible({ timeout: 15_000 })
  await removeNamed(page, name)
})

test('커넥터를 설치하면 MCP 서버가 실제로 도구를 보고한다', async ({ page }) => {
  await page.goto('/connectors')

  // Scoped to the 시간 card: "22시간 전" is on the screen too, and the first entry may need credentials.
  const mine = page.getByRole('tab', { name: /내 커넥터/ })
  await page.getByRole('tab', { name: /카탈로그/ }).click()
  const install = page
    .locator('[data-connector="time"]')
    .getByRole('button', { name: /추가|설치됨/ })
  await expect(install).toBeVisible({ timeout: 15_000 })

  if (await install.isEnabled()) {
    // Installing spawns the server; uv may fetch the package on a cold cache.
    await install.click()
  }
  await mine.click()

  await expect(page.getByText('시간').first()).toBeVisible({ timeout: 180_000 })
  // 연결됨 plus a non-zero tool count means the server answered tools/list.
  await expect(page.getByText('연결됨').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText(/도구 [1-9]/).first()).toBeVisible({ timeout: 60_000 })

  await page.reload()
  await mine.click()
  await expect(page.getByText(/도구 [1-9]/).first()).toBeVisible({ timeout: 30_000 })
})

test('첨부한 파일의 내용을 모델이 읽는다', async ({ page }) => {
  await page.goto('/new/chat')
  await page.getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i }).first().click()
  await page.getByRole('button', { name: /qwen3\.6/i }).first().click()

  const token = `SENTINEL-${stamp().toUpperCase()}`
  await page.getByLabel('파일 선택').setInputFiles({
    name: 'note.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from(`이 문서의 비밀 코드는 ${token} 이다.`, 'utf-8'),
  })
  await expect(page.getByText('note.txt')).toBeVisible({ timeout: 30_000 })

  await askFromNew(page, '첨부 파일의 비밀 코드를 그대로 알려줘.')
  await expect(answerText(page, token)).toBeVisible({ timeout: 120_000 })
})

test('웹 검색 토글을 켜면 실제로 검색 단계가 보인다', async ({ page }) => {
  await page.goto('/new/chat')
  await pickToolModel(page)

  await page.getByRole('button', { name: /웹 검색/ }).first().click()
  await askFromNew(page, '올해 노벨 물리학상 수상자를 웹에서 확인해줘.')
  await expect(page.getByText(/웹 검색/).first()).toBeVisible({ timeout: 120_000 })
})
