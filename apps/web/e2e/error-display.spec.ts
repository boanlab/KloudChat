import { expect, test } from '@playwright/test'
import { composerFixture, type TurnReply } from './composer-fixture'

/** A failed turn shows its error in the interface, beside whatever it managed to write, with the reason. */

async function ask(page: import('@playwright/test').Page, testInfo: import('@playwright/test').TestInfo, text: string, reply: TurnReply) {
  await composerFixture(page, testInfo, { turn: () => reply })
  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill(text)
  await page.getByLabel('프롬프트 입력').press('Enter')
}

test('실패한 답변은 쓰다 만 내용과 오류를 함께 보여 준다', async ({ page }, testInfo) => {
  await ask(page, testInfo, '오류 표시 확인', { events: [
    { type: 'delta', text: '여기까지는 썼습니다.' },
    { type: 'error', message: '모델이 응답을 끝내지 못했습니다.' },
  ] })
  const failure = page.getByText('모델이 응답을 끝내지 못했습니다.')
  await expect(failure).toBeVisible({ timeout: 15_000 })
  // The partial answer is kept.
  await expect(page.getByText('여기까지는 썼습니다.')).toBeVisible()
  await expect(page.getByText('⚠️')).toHaveCount(0)
  await expect(page.getByText('답을 쓰는 중…')).toHaveCount(0)
})

test('내용을 하나도 못 받은 실패도 오류로 보인다', async ({ page }, testInfo) => {
  await ask(page, testInfo, '빈 응답 오류 확인', { events: [{ type: 'error', message: '요청이 거부되었습니다.' }] })
  await expect(page.getByText('요청이 거부되었습니다.')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('답을 쓰는 중…')).toHaveCount(0)
})

test('실패는 사유를 말한다 — 모델 서버에 닿지 못한 경우', async ({ page }, testInfo) => {
  // The server sends a code and the upstream's reason alongside the message.
  await ask(page, testInfo, '사유 표시 확인', { events: [{
    type: 'error',
    code: 'upstream_unreachable',
    reason: 'connect ECONNREFUSED 10.0.0.5:8080',
    message: '모델 응답을 받지 못했습니다.',
  }] })
  const notice = page.getByRole('status').filter({ hasText: '모델 서버에 연결할 수 없습니다' })
  await expect(notice).toBeVisible({ timeout: 15_000 })
  await expect(notice).toContainText('ECONNREFUSED')
  await expect(page.getByText('모델 응답을 받지 못했습니다.')).toHaveCount(0)
})

test('실패는 사유를 말한다 — 꺼진 에이전트에 보낸 경우', async ({ page }, testInfo) => {
  // A 422 before the turn is written carries a machine code.
  await ask(page, testInfo, '꺼진 에이전트 확인', { status: 422, json: { detail: 'agent_disabled' } })
  await expect(page.getByText(/에이전트가 꺼져 있어/).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('요청을 처리하지 못했습니다.')).toHaveCount(0)
})
