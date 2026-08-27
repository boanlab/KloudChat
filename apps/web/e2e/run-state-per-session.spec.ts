import { expect, test } from '@playwright/test'
import { openSidebar, signIn } from './helpers'

/**
 * Run state belongs to the conversation that owns the turn. With one global
 * flag, opening any other conversation while one generated showed a stop
 * button where send belonged and a caret after an answer that had finished
 * days ago — and that stop went to the wrong session.
 *
 * Needs a real model: the turn in A has to still be running when B opens.
 * Uses the local Qwen so it costs nothing.
 */
test('다른 대화가 생성 중이어도 끝난 대화는 끝난 대로 보인다', async ({ page }) => {
  test.setTimeout(240_000)
  await signIn(page)

  // B: finished long ago, from the API so it carries no run state of its own.
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

  // A: a turn long enough to still be streaming a few seconds in.
  await page.goto('/new/chat')
  await page.getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i }).first().click()
  await page.getByRole('button', { name: /qwen3\.6/i }).first().click()
  await page.getByLabel('프롬프트 입력').fill('우주 탐사의 역사를 연도별로 2,000자 이상 아주 자세히 써줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  const aUrl = page.url()
  await expect(page.getByLabel('중지')).toBeVisible({ timeout: 20_000 })

  // Open B by the sidebar — a route change, not a reload, so A keeps streaming.
  await openSidebar(page)
  await page.locator('aside').getByText(title).first().click()
  await expect(page).not.toHaveURL(aUrl)

  // B is finished: send is offered, stop is not.
  await expect(page.getByLabel('전송')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0)

  // Back in A the turn is still its own — and it settles on its own.
  await page.goto(aUrl)
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 180_000 })
})
