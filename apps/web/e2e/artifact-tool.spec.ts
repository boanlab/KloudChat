import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Chat makes a thing when it is asked for a thing.
 *
 * The model decides whether to create an artifact. Deciding it from whether
 * the answer contains a long code fence cannot tell "build me a page" from
 * "here is an example" — and this request is one that leaves only prose under
 * that rule.
 */
test('페이지를 만들어 달라고 하면 아티팩트가 생긴다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)
  await page.goto('/new/chat')
  await page
    .getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i })
    .first()
    .click()
  await page.getByRole('button', { name: /qwen3\.6/i }).first().click()

  await page.getByLabel('프롬프트 입력').fill(
    'AI 보안 소개 페이지를 만들어줘. 제목, 소개, 주요 위협 3가지로 구성된 한 페이지 HTML 로.',
  )
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // The step is how a reader knows the document came from a decision rather
  // than from something the interface guessed at afterwards.
  await expect(page.getByText('아티팩트 만드는 중')).toBeVisible({ timeout: 240_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 240_000 })

  // It is a stored artifact, of the kind asked for, with a real document in it.
  const stored = await page.evaluate(async () => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = await login.json()
    const rows = await (
      await fetch('/api/artifacts', { headers: { Authorization: `Bearer ${accessToken}` } })
    ).json()
    const list = Array.isArray(rows) ? rows : rows.items
    const row = list.find((a: { kind: string }) => a.kind === 'html')
    // The listing sends HTML documents without their markup. Fetch the file.
    if (!row) return null
    return await (
      await fetch('/api/artifacts/' + row.id, {
        headers: { Authorization: `Bearer ${accessToken}` },
      })
    ).json()
  })
  expect(stored, 'html 아티팩트가 만들어지지 않았습니다').not.toBeNull()
  expect(String(stored.data.content).toLowerCase()).toContain('<html')
  expect(stored.title.length).toBeGreaterThan(1)
})
