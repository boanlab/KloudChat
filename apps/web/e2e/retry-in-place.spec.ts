import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** 다시 시도 reruns the failed turn in place, naming it with `retryOf`. Stream faked. */
test('다시 시도는 질문을 두 번 남기지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const prompt = `재시도 확인 ${Date.now().toString(36)}`
  const bodies: Array<Record<string, unknown>> = []
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    const body = route.request().postDataJSON() as Record<string, unknown>
    bodies.push(body)
    const event = body.retryOf
      ? { type: 'delta', text: '이번에는 답했습니다.' }
      : { type: 'error', message: '모델 응답을 받지 못했습니다.' }
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: `data: ${JSON.stringify(event)}\n\n`,
    })
  })

  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill(prompt)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page.getByText('모델 응답을 받지 못했습니다.').first()).toBeVisible({ timeout: 30_000 })

  await page.getByRole('button', { name: '다시 시도' }).first().click()

  await expect(page.getByText('이번에는 답했습니다.')).toBeVisible({ timeout: 30_000 })
  // One question on screen; the retry named it.
  await expect(page.getByText(prompt, { exact: true })).toHaveCount(1)
  await expect(page.getByText('모델 응답을 받지 못했습니다.')).toHaveCount(0)
  expect(bodies).toHaveLength(2)
  expect(bodies[0].retryOf).toBeUndefined()
  expect(typeof bodies[1].retryOf).toBe('string')
})
