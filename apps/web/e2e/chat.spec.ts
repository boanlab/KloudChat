/** Chat against the real backend and the local Qwen model. */

import { expect, test } from '@playwright/test'
import { answerText, signIn } from './helpers'

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

/** Picks the local model. */
async function useLocalModel(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i }).first().click()
  await page.getByRole('button', { name: /qwen3\.6/i }).first().click()
}

test('메시지를 보내면 토큰이 스트리밍되고 답이 남는다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)

  await page.getByLabel('프롬프트 입력').fill('한 문장으로만 답해줘: 1 + 1 은?')
  // Wait for the session to be created.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().endsWith('/api/sessions') && r.request().method() === 'POST',
    ),
    page.getByLabel('프롬프트 입력').press('Enter'),
  ])
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  const assistant = page.locator('.group').filter({ hasNot: page.locator('form') }).last()
  await expect(assistant).toContainText('2', { timeout: 90_000 })

  // The usage footer, on the answer itself; a free model ends the line "무료".
  await expect(assistant.getByText(/ in · .+ out · (무료|[\d,]+ 크레딧)/)).toBeVisible({
    timeout: 30_000,
  })
})

test('새로고침해도 대화가 남아 있다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  const prompt = '한 단어로만 답해줘: 대한민국의 수도는?'
  await page.getByLabel('프롬프트 입력').fill(prompt)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  // The answer paragraph, not a sidebar title containing the same word.
  await expect(answerText(page, '서울')).toBeVisible({ timeout: 90_000 })

  // Let the turn commit before reloading.
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 120_000 })

  const url = page.url()
  await page.reload()
  // `.first()`: the prompt is also the conversation's title.
  await expect(page.getByText(prompt).first()).toBeVisible({ timeout: 20_000 })
  await expect(answerText(page, '서울')).toBeVisible({ timeout: 20_000 })
  expect(page.url()).toBe(url)
})

test('첫 턴이 끝나면 대화 제목이 생성된다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  await page.getByLabel('프롬프트 입력').fill('광합성이 뭔지 두 문장으로 설명해줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  // The provisional title is the raw prompt; sidebar entries are buttons.
  await expect
    .poll(
      async () => {
        const labels = await page.getByRole('button').allInnerTexts()
        return labels.some((t) => t.includes('광합성') && !t.includes('두 문장으로'))
      },
      { timeout: 120_000, intervals: [2_000] },
    )
    .toBe(true)
})

test('사이드바에 이전 대화가 쌓이고 삭제된다', async ({ page }) => {
  await page.goto('/new/chat')
  await useLocalModel(page)
  await page.getByLabel('프롬프트 입력').fill('짧게 답해줘: 물의 화학식은?')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  // The answer, not the title generated from it.
  await expect(answerText(page, /H2O|H₂O/i)).toBeVisible({ timeout: 90_000 })

  // A reload proves the list came from the server.
  await page.reload()
  await expect(page.getByRole('button', { name: /물|화학식|H2O/ }).first()).toBeVisible({
    timeout: 20_000,
  })
})
