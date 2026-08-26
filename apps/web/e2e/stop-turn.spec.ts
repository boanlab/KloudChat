import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Pressing 중단 has to show on the screen it was pressed on. The answer used
 * to end mid-sentence with nothing to say why, the retry appeared only after
 * a reload, and the reload then called the reader's own stop an error and
 * priced it at 0 in · 0 out.
 *
 * Needs a real model; uses the local Qwen so it costs nothing.
 */
test('중단은 그 자리에서 보이고, 다시 열어도 같은 말을 한다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  await page.goto('/new/chat')
  await page.getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i }).first().click()
  await page.getByRole('button', { name: /qwen3\.6/i }).first().click()
  await page.getByLabel('프롬프트 입력').fill('우주 탐사의 역사를 연도별로 2,000자 이상 아주 자세히 써줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  // Let a few tokens land, then stop.
  const assistant = page.locator('.group').filter({ hasNot: page.locator('form') }).last()
  await expect(assistant.locator('p').first()).toBeVisible({ timeout: 60_000 })
  await Promise.all([
    page.waitForResponse((r) => /\/api\/sessions\/[0-9a-f]{32}\/stop$/.test(r.url())),
    page.getByLabel('중지').click(),
  ])

  // Right away, without a reload: the mark and the way back.
  const notice = assistant.getByRole('status')
  await expect(notice).toContainText('여기서 멈췄습니다', { timeout: 5_000 })
  await expect(notice.getByRole('button', { name: '다시 시도' })).toBeVisible()

  // The server settles the turn on its own; reopened, it says the same thing
  // and the usage is a figure, not 0 in · 0 out.
  await page.waitForTimeout(3_000)
  await page.reload()
  const reopened = page.locator('.group').filter({ hasNot: page.locator('form') }).last()
  await expect(reopened.getByRole('status')).toContainText('여기서 멈췄습니다', { timeout: 20_000 })
  await expect(reopened.getByText(/≈ .+ in · .+ out/)).toBeVisible({ timeout: 20_000 })
  await expect(reopened.getByText(/\b0 in · 0 out\b/)).toHaveCount(0)
})
