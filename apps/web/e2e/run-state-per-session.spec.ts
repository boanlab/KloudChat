import { expect, test } from '@playwright/test'
import { openSidebar, signIn } from './helpers'

/** Run state belongs to the conversation that owns the turn. Needs a real model; the local Qwen costs nothing. */
test('다른 대화가 생성 중이어도 끝난 대화는 끝난 대로 보인다', async ({ page }) => {
  test.setTimeout(240_000)
  await signIn(page)

  // B: a finished conversation, from the API.
  const title = `끝난대화 ${Date.now().toString(36)}`
  await page.evaluate(async (name) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = await login.json()
    const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` }
    const r = await fetch('/api/sessions', { method: 'POST', headers: H, body: JSON.stringify({ kind: 'chat' }) })
    const { id } = await r.json()
    await fetch(`/api/sessions/${id}`, { method: 'PATCH', headers: H, body: JSON.stringify({ title: name }) })
  }, title)

  // A: a turn long enough to still be streaming.
  await page.goto('/new/chat')
  await page.getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i }).first().click()
  await page.getByRole('button', { name: /qwen3\.6/i }).first().click()
  await page.getByLabel('프롬프트 입력').fill('우주 탐사의 역사를 연도별로 2,000자 이상 아주 자세히 써줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  const aUrl = page.url()
  await expect(page.getByLabel('중지')).toBeVisible({ timeout: 20_000 })

  // A route change, not a reload, so A keeps streaming.
  await openSidebar(page)
  await page.locator('aside').getByText(title).first().click()
  await expect(page).not.toHaveURL(aUrl)

  await expect(page.getByLabel('전송')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0)

  // A settles on its own.
  await page.goto(aUrl)
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 180_000 })
})
