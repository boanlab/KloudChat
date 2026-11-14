import { expect, test } from '@playwright/test'
import { composerFixture } from './composer-fixture'

test('보낸 프롬프트도 복사할 수 있다', async ({ page }, testInfo) => {
  await composerFixture(page, testInfo, { turn: () => ({ events: [{ type: 'delta', text: '답변입니다.' }, { type: 'done' }] }) })
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])

  const prompt = '복사 확인용 프롬프트'
  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill(prompt)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page.getByText('답변입니다.')).toBeVisible({ timeout: 15_000 })

  await page.getByRole('button', { name: '프롬프트 복사' }).first().click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(prompt)
})
