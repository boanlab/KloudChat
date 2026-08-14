import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('보낸 프롬프트도 복사할 수 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  // A fake stream: this is about the button next to the prompt, and waiting on a
  // model would make it a test of the model.
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: `data: ${JSON.stringify({ type: 'delta', text: '답변입니다.' })}\n\n`,
    })
  })
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])

  const prompt = `복사 확인 ${Date.now()}`
  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill(prompt)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page.getByText('답변입니다.')).toBeVisible({ timeout: 30_000 })

  await page.getByRole('button', { name: '프롬프트 복사' }).first().click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(prompt)
})
