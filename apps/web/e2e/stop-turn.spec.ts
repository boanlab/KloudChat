import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** 중단 shows in place with a retry, and the reopened turn says the same with real usage.
 *  Needs a real model; the local Qwen costs nothing. */
test('중단은 그 자리에서 보이고, 다시 열어도 같은 말을 한다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  await page.goto('/new/chat')
  await page.getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i }).first().click()
  await page.getByRole('button', { name: /qwen3\.6/i }).first().click()
  await page.getByLabel('프롬프트 입력').fill('우주 탐사의 역사를 연도별로 2,000자 이상 아주 자세히 써줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  // The 중지 button is the proof of streaming; waiting for a paragraph races a short answer.
  const assistant = page.locator('.group').filter({ hasNot: page.locator('form') }).last()
  await expect(page.getByLabel('중지')).toBeVisible({ timeout: 60_000 })
  // The usage assertion needs at least one token; wait briefly for a paragraph, but do not fail.
  await expect(assistant.locator('p').first())
    .toBeVisible({ timeout: 20_000 })
    .catch(() => {})
  await Promise.all([
    page.waitForResponse((r) => /\/api\/sessions\/[0-9a-f]{32}\/stop$/.test(r.url()), {
      timeout: 30_000,
    }),
    page.getByLabel('중지').click(),
  ])
  // In place, without a reload: the mark and the retry.
  const notice = assistant.getByRole('status')
  await expect(notice).toContainText('여기서 멈췄습니다', { timeout: 5_000 })
  await expect(notice.getByRole('button', { name: '다시 시도' })).toBeVisible()

  // The server settles the turn; reopened, usage is a figure, not 0 in · 0 out.
  await page.waitForTimeout(3_000)
  await page.reload()
  const reopened = page.locator('.group').filter({ hasNot: page.locator('form') }).last()
  await expect(reopened.getByRole('status')).toContainText('여기서 멈췄습니다', { timeout: 20_000 })
  await expect(reopened.getByText(/≈ .+ in · .+ out/)).toBeVisible({ timeout: 20_000 })
  await expect(reopened.getByText(/\b0 in · 0 out\b/)).toHaveCount(0)
})
