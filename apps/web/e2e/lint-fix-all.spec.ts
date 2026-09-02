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
  '여기에 내용을 입력하세요.',
  '혁신적인 접근으로 해결합니다.',
  '',
  '## 성과',
  '',
  '🚀 빠르게 성장하고 있습니다.',
].join('\n')

test('모두 고치기는 절마다 한 번씩만 다시 쓴다', async ({ page }) => {
  const rewrites: { sectionId: string; note: string }[] = []
  await page.route('**/api/artifacts/*/sections/*/rewrite', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    const body = route.request().postDataJSON() as { note: string }
    const sectionId = new URL(route.request().url()).pathname.split('/').at(-2) ?? ''
    rewrites.push({ sectionId, note: body.note })
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
  // A successful rewrite refreshes the artifact and can legitimately remove
  // the findings menu before its transient confirmation is painted. The
  // durable proof is the set of rewrite requests below.
  await expect.poll(() => rewrites.length, { timeout: 60_000 }).toBeGreaterThan(0)

  // The point of the whole thing: no section asked to rewrite twice.
  const seen = rewrites.map((r) => r.sectionId)
  expect(new Set(seen).size, `같은 절을 두 번 다시 썼다: ${seen.join(', ')}`).toBe(seen.length)
  expect(rewrites.length, '한 절도 다시 쓰지 않았다').toBeGreaterThan(0)
  // And every note is well formed: one problem stated plainly, several as a
  // numbered list. A list of one is the shape that means somebody grouped and
  // then sent the group one item at a time.
  for (const rewrite of rewrites) {
    const numbered = rewrite.note.match(/^\d+\. /gm)?.length ?? 0
    expect(numbered === 0 || numbered > 1, `번호가 하나뿐인 목록: ${rewrite.note}`).toBe(true)
  }

  // Nothing is invented. The notes cannot name more problems than the checker
  // found — and they may name fewer, because a finding no section owns is not
  // dropped but sent to the conversation as one message instead.
  const named = rewrites.reduce(
    (total, rewrite) => total + Math.max(1, rewrite.note.match(/^\d+\. /gm)?.length ?? 0),
    0,
  )
  expect(named, '지적보다 많은 문제를 지어냈다').toBeLessThanOrEqual(count)
})
