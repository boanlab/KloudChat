/**
 * 모두 고치기 — every finding in one press.
 *
 * The button exists because the list is long. A document that says 99.9% in
 * three sections gets one finding per section, and fixing them one at a time
 * means three presses and three rewrites — with the reader waiting through
 * each. What it must NOT do is rewrite the same part once per finding: a
 * rewrite works on the text the last one produced, so the second is asked to
 * fix a sentence that is no longer there and writes the first fix back out.
 *
 * So the claim under test is not "the button works" but "one part is rewritten
 * once, and told everything found in it".
 */
import { expect, test } from '@playwright/test'
import { openAndSeedReport } from './helpers'

const BODY = [
  '## 배경',
  '',
  '가용성은 99.9% 였다.',
  '',
  '## 성과',
  '',
  '오탐률은 32% 줄었다.',
].join('\n')

test('모두 고치기는 절마다 한 번씩만 다시 쓴다', async ({ page }) => {
  const rewrites: { sectionId: string; note: string }[] = []
  await page.route('**/api/artifacts/*/sections/rewrite', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    rewrites.push(route.request().postDataJSON() as { sectionId: string; note: string })
    // Answered with an unchanged document: this is about what is asked for,
    // not about what a model writes back.
    await route.fulfill({ json: { id: 'x', version: 99, data: {} } })
  })

  // The findings are whatever the linter makes of this body — the button is
  // the subject, not the rules — so the case skips rather than lies if this
  // document happens to draw fewer than two.
  await openAndSeedReport(page, BODY)

  const badge = page.getByRole('button', { name: '검사 결과' })
  await expect(badge).toBeVisible({ timeout: 20_000 })
  await badge.click()

  const all = page.getByRole('button', { name: /모두 고치기/ })
  if ((await all.count()) === 0) {
    test.skip(true, '이 문서에는 지적이 둘 이상 없습니다')
  }
  const label = await all.textContent()
  const count = Number((label ?? '').match(/\((\d+)\)/)?.[1] ?? '0')
  expect(count, '버튼이 지적 개수를 말하지 않는다').toBeGreaterThan(1)

  await all.click()
  await expect(page.getByText('모두 고쳤습니다.')).toBeVisible({ timeout: 60_000 })

  // The point of the whole thing: no section asked to rewrite twice.
  const seen = rewrites.map((r) => r.sectionId)
  expect(new Set(seen).size, `같은 절을 두 번 다시 썼다: ${seen.join(', ')}`).toBe(seen.length)
  expect(rewrites.length, '한 절도 다시 쓰지 않았다').toBeGreaterThan(0)
  // And a part with more than one finding is told about all of them at once.
  const many = rewrites.find((r) => /\n2\./.test(r.note))
  if (count > seen.length) {
    expect(many, '한 절에 여러 지적이 있는데 하나만 전달했다').toBeTruthy()
  }
})
