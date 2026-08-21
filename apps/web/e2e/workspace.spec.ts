/**
 * The workspace screens against a real backend: does a project survive a
 * reload, is an uploaded file actually read, does a connector start a server.
 * `personas.spec.ts` asks whether the capability exists; this asks whether it
 * works.
 *
 * Requires the API running. Run: npm run test:workspace
 */

import { expect, test } from '@playwright/test'
import { answerText, signIn } from './helpers'

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

/** Unique per run so repeated runs never collide on a name. */
const stamp = () => Math.random().toString(36).slice(2, 8)

/** Sends from `/new/chat` and waits for the session: navigation follows the
 *  round trip, so asserting on the URL immediately races it. */
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

/** Removes what the tests created, so the suite can be pointed at a real
 *  instance without filling its screens with debris. */
/**
 * Deletes a row and waits for the request, not just the screen.
 *
 * The row leaves at once and the call is held for the undo window, so a
 * context closed in between leaves the row on the instance.
 */
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
  // Scoped to the dialog. `.last()` on the whole page picks up whatever else
  // happens to be labelled "add" or "save" — and when the dialog is a frame
  // slower than the click, it picks one of those instead and nothing is
  // created.
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
  // Memories are `global` scope, so one left behind joins every later turn's
  // prompt — on a long-lived instance the suite ends up testing against its own
  // debris.
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

  // Asked of the catalogue, not by searching for 시간 elsewhere: "22시간 전"
  // is on that screen too. Scoped to the 시간 card, never `.first()` — the
  // first entry sorts arbitrarily and may open a credential modal.
  const mine = page.getByRole('tab', { name: /내 커넥터/ })
  await page.getByRole('tab', { name: /카탈로그/ }).click()
  const install = page
    .locator('[data-connector="time"]')
    .getByRole('button', { name: /추가|설치됨/ })
  await expect(install).toBeVisible({ timeout: 15_000 })

  if (await install.isEnabled()) {
    // Installing spawns the server and asks it for tools; uv may fetch the
    // package on a cold cache, so this is genuinely slow the first time.
    await install.click()
  }
  await mine.click()

  await expect(page.getByText('시간').first()).toBeVisible({ timeout: 180_000 })
  // "connected" plus a non-zero tool count means the server answered tools/list.
  // A flag flip alone would show neither.
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

/**
 * A conversation model that can reach the network.
 *
 * The screen default is a strict-local model, which is given no web tool at
 * all — so a spec about searching has to say which model it means. Picked by
 * excluding the Strict Local group rather than by naming an id, because the
 * row prints the model's name and not its route.
 */
async function pickSearchable(page: import('@playwright/test').Page) {
  await page
    .getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i })
    .first()
    .click()
  const rows = page.getByRole('button', { name: /qwen3\.6/i })
  const count = await rows.count()
  for (let i = 0; i < count; i++) {
    const name = (await rows.nth(i).getAttribute('aria-label')) ?? (await rows.nth(i).innerText())
    if (!/strict/i.test(name)) {
      await rows.nth(i).click()
      return
    }
  }
  throw new Error('웹에 닿는 모델을 고르지 못했습니다')
}

test('웹 검색 토글을 켜면 실제로 검색 단계가 보인다', async ({ page }) => {
  await page.goto('/new/chat')
  await pickSearchable(page)

  await page.getByRole('button', { name: /웹 검색/ }).first().click()
  await askFromNew(page, '올해 노벨 물리학상 수상자를 웹에서 확인해줘.')
  await expect(page.getByText(/웹 검색/).first()).toBeVisible({ timeout: 120_000 })
})
