import { expect, test } from '@playwright/test'
import { signIn, storedArtifacts } from './helpers'

/** Asking chat for a page produces a stored html artifact via the tool. */
test('페이지를 만들어 달라고 하면 아티팩트가 생긴다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)
  await page.goto('/new/chat')
  await page
    .getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i })
    .first()
    .click()
  // A model that calls tools reliably.
  await page.getByRole('button', { name: /qwen3\.5|qwen3\.6/i }).first().click()

  await page.getByLabel('프롬프트 입력').fill(
    'AI 보안 소개 페이지를 만들어줘. 제목, 소개, 주요 위협 3가지로 구성된 한 페이지 HTML 로.',
  )
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // The tool step, live or already collapsed into the summary.
  await expect(page.getByText(/아티팩트 (만드는 중|생성)/).first()).toBeVisible({ timeout: 240_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 240_000 })

  // Stored, of the kind asked for, with a real document in it.
  const [stored] = await storedArtifacts(page, 'html')
  expect(stored, 'html 아티팩트가 만들어지지 않았습니다').toBeTruthy()
  expect(String(stored.data.content).toLowerCase()).toContain('<html')
  expect(stored.title.length).toBeGreaterThan(1)
})
