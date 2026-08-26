import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A turn that fails says so in the interface, not by prefixing its own text with
 * a warning emoji — and it says so *next to* whatever it managed to write. The
 * old shape lost both halves: with any content already streamed the failure was
 * silent, and with none it replaced the answer with "⚠️ …".
 */
test('실패한 답변은 쓰다 만 내용과 오류를 함께 보여 준다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // The stream is faked so the failure is exact and costs no model call.
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body:
        `data: ${JSON.stringify({ type: 'delta', text: '여기까지는 썼습니다.' })}\n\n` +
        `data: ${JSON.stringify({ type: 'error', message: '모델이 응답을 끝내지 못했습니다.' })}\n\n`,
    })
  })

  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('오류 표시 확인')
  await page.getByLabel('프롬프트 입력').press('Enter')

  const failure = page.getByText('모델이 응답을 끝내지 못했습니다.')
  await expect(failure).toBeVisible({ timeout: 30_000 })
  // The partial answer is kept, not replaced.
  await expect(page.getByText('여기까지는 썼습니다.')).toBeVisible()
  // No emoji standing in for the interface.
  await expect(page.getByText('⚠️')).toHaveCount(0)
  // And it does not look like it is still running.
  await expect(page.getByText('생각하는 중…')).toHaveCount(0)
})

test('내용을 하나도 못 받은 실패도 오류로 보인다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: `data: ${JSON.stringify({ type: 'error', message: '요청이 거부되었습니다.' })}\n\n`,
    })
  })

  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('빈 응답 오류 확인')
  await page.getByLabel('프롬프트 입력').press('Enter')

  await expect(page.getByText('요청이 거부되었습니다.')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('생각하는 중…')).toHaveCount(0)
})

test('실패는 사유를 말한다 — 모델 서버에 닿지 못한 경우', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // What the server now sends: the sentence it always did, plus the machine
  // code and the upstream's own reason. The screen has to use them.
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body: `data: ${JSON.stringify({
        type: 'error',
        code: 'upstream_unreachable',
        reason: 'connect ECONNREFUSED 10.0.0.5:8080',
        message: '모델 응답을 받지 못했습니다.',
      })}\n\n`,
    })
  })

  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('사유 표시 확인')
  await page.getByLabel('프롬프트 입력').press('Enter')

  const notice = page.getByRole('status').filter({ hasText: '모델 서버에 연결할 수 없습니다' })
  await expect(notice).toBeVisible({ timeout: 30_000 })
  await expect(notice).toContainText('ECONNREFUSED')
  await expect(page.getByText('모델 응답을 받지 못했습니다.')).toHaveCount(0)
})

test('실패는 사유를 말한다 — 꺼진 에이전트에 보낸 경우', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // A refusal before the turn is written: the API answers 422 with a machine
  // code, which used to be blanked into "요청을 처리하지 못했습니다".
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 422,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ detail: 'agent_disabled' }),
    })
  })

  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('꺼진 에이전트 확인')
  await page.getByLabel('프롬프트 입력').press('Enter')

  await expect(page.getByText(/에이전트가 꺼져 있어/).first()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('요청을 처리하지 못했습니다.')).toHaveCount(0)
})
