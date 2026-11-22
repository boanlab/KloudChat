import { expect, test } from '@playwright/test'
import { composerFixture } from './composer-fixture'

/**
 * A send that fails without a refusal (server error, dropped connection) leaves the question in the
 * thread with one failure message and 「다시 시도」, fully above the composer; the composer does not
 * repeat the text or a second error. Fixture data only.
 */
const prompt = '전송 실패 확인용 질문'

for (const viewport of [{ name: 'pc', width: 1280, height: 800 }, { name: 'phone', width: 390, height: 780 }]) {
  test(`${viewport.name}: 서버 오류는 대화 안에 한 번만, 입력창 위에 다 보이게 알린다`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await composerFixture(page, testInfo, {
      turn: (body) => body.retryOf
        ? { events: [{ type: 'delta', text: '이번에는 답했습니다.' }, { type: 'done' }] }
        : { status: 500, json: { detail: 'internal' } },
    })
    await page.goto('/new/chat')
    const input = page.getByLabel('프롬프트 입력')
    await input.fill(prompt)
    await input.press('Enter')

    const failure = page.getByText('서버 오류로 요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.')
    await expect(failure).toBeVisible({ timeout: 15_000 })
    // One message, not two: nothing in the composer area repeats it.
    await expect(failure).toHaveCount(1)
    await expect(page.getByText('요청을 전송하지 못했습니다.')).toHaveCount(0)
    // The question is shown once (the thread's), and the box is empty again.
    await expect(page.getByText(prompt, { exact: true })).toHaveCount(1)
    await expect(input).toHaveValue('')

    const retry = page.getByRole('button', { name: '다시 시도' }).first()
    await expect(retry).toBeInViewport({ ratio: 1 })
    const retryBox = (await retry.boundingBox())!
    const composerBox = (await input.boundingBox())!
    expect(retryBox.y + retryBox.height).toBeLessThanOrEqual(composerBox.y)

    await retry.click()
    await expect(page.getByText('이번에는 답했습니다.')).toBeVisible({ timeout: 15_000 })
  })
}

test('연결이 끊긴 전송도 질문과 실패 안내를 대화에 한 번만 남긴다', async ({ page }, testInfo) => {
  await composerFixture(page, testInfo, { turn: () => ({ events: [{ type: 'delta', text: '답' }, { type: 'done' }] }) })
  await page.route('**/api/sessions/*/messages', (route) =>
    route.request().method() === 'POST' ? route.abort('failed') : route.fallback())
  await page.goto('/new/chat')
  const input = page.getByLabel('프롬프트 입력')
  await input.fill(prompt)
  await input.press('Enter')
  await expect(page.getByText('응답을 받지 못했습니다. 잠시 후 다시 시도하세요.')).toHaveCount(1, { timeout: 15_000 })
  await expect(page.getByText(prompt, { exact: true })).toHaveCount(1)
  await expect(input).toHaveValue('')
  await expect(page.getByRole('button', { name: '다시 시도' }).first()).toBeInViewport({ ratio: 1 })
})
