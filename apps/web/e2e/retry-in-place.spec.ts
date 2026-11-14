import { expect, test } from '@playwright/test'
import { composerFixture } from './composer-fixture'

/** 다시 시도 reruns the failed turn in place, naming it with `retryOf`. */
test('다시 시도는 질문을 두 번 남기지 않는다', async ({ page }, testInfo) => {
  const prompt = '재시도 확인용 질문'
  const { sends } = await composerFixture(page, testInfo, {
    turn: (body) => ({ events: body.retryOf
      ? [{ type: 'delta', text: '이번에는 답했습니다.' }, { type: 'done' }]
      : [{ type: 'error', message: '모델 응답을 받지 못했습니다.' }] }),
  })

  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill(prompt)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page.getByText('모델 응답을 받지 못했습니다.').first()).toBeVisible({ timeout: 15_000 })

  await page.getByRole('button', { name: '다시 시도' }).first().click()

  await expect(page.getByText('이번에는 답했습니다.')).toBeVisible({ timeout: 15_000 })
  // One question on screen; the retry named it.
  await expect(page.getByText(prompt, { exact: true })).toHaveCount(1)
  await expect(page.getByText('모델 응답을 받지 못했습니다.')).toHaveCount(0)
  expect(sends).toHaveLength(2)
  expect(sends[0].body.retryOf).toBeUndefined()
  expect(typeof sends[1].body.retryOf).toBe('string')
})
