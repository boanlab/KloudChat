/** 모두 고치기 rewrites each section once, with every finding in it grouped into one note. */
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
  // The section id is in the body: `/sections/rewrite` + `{ sectionId }`.
  await page.route('**/api/artifacts/*/sections/rewrite', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    const body = route.request().postDataJSON() as { sectionId: string; note: string }
    rewrites.push({ sectionId: body.sectionId, note: body.note })
    // Answered with an unchanged document.
    await route.fulfill({ json: { id: 'x', version: 99, data: {} } })
  })

  // Skips if the linter finds fewer than two problems in this body.
  await openAndSeedReport(page, BODY)

  await page.getByRole('tab', { name: '검토', exact: true }).click()
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
  // The rewrite requests are the proof; the findings menu may close before any confirmation.
  await expect.poll(() => rewrites.length, { timeout: 60_000 }).toBeGreaterThan(0)

  // No section rewritten twice.
  const seen = rewrites.map((r) => r.sectionId)
  expect(new Set(seen).size, `같은 절을 두 번 다시 썼다: ${seen.join(', ')}`).toBe(seen.length)
  expect(rewrites.length, '한 절도 다시 쓰지 않았다').toBeGreaterThan(0)
  // One problem stated plainly, several as a numbered list; never a list of one.
  for (const rewrite of rewrites) {
    const numbered = rewrite.note.match(/^\d+\. /gm)?.length ?? 0
    expect(numbered === 0 || numbered > 1, `번호가 하나뿐인 목록: ${rewrite.note}`).toBe(true)
  }

  // Notes name no more problems than the checker found (fewer is fine: unowned findings go to the conversation).
  const named = rewrites.reduce(
    (total, rewrite) => total + Math.max(1, rewrite.note.match(/^\d+\. /gm)?.length ?? 0),
    0,
  )
  expect(named, '지적보다 많은 문제를 지어냈다').toBeLessThanOrEqual(count)
})
