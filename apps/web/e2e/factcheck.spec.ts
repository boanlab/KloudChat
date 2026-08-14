import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Slide fact-checking.
 *
 * **A confident verdict always carries a source.** That is the rule this spec
 * guards: a "supported" badge with no evidence behind it stops the reader
 * looking precisely where they should. Costs one search and one model call per
 * claim.
 */

const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, {
    ...(init || {}),
    headers: { ...((init || {}).headers || {}), Authorization: 'Bearer ' + accessToken },
  })
  // 204 has no body — the cleanup DELETE at the end goes through here too.
  if (!r.ok || r.status === 204) return null
  return await r.json()
}`

test('확신 있는 판정에는 반드시 근거 링크가 붙는다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)

  // A slide built to exercise all three outcomes: a checkable number that is
  // wrong, and a pure opinion that must not be extracted at all.
  const deck = await page.evaluate(async (fn) => {
    return await eval(fn)('/api/artifacts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind: 'deck',
        title: `팩트체크 확인 ${Date.now().toString(36)}`,
        data: {
          kind: 'deck',
          theme: '기본',
          slides: [
            {
              id: 'fc-check',
              layout: 'bullets',
              title: '확인 대상',
              accent: '#5b5bd6',
              bullets: [
                '2024년 국내 전기차 등록대수는 정확히 731,884대였다',
                '우리 방식이 경쟁사보다 훨씬 낫다',
              ],
            },
          ],
        },
      }),
    })
  }, AS_USER)
  expect(deck?.id, '검증용 덱을 만들지 못했습니다').toBeTruthy()

  const checked = await page.evaluate(
    async ([fn, id]) =>
      await eval(fn as string)(`/api/artifacts/${id}/slides/factcheck`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slideId: 'fc-check' }),
      }),
    [AS_USER, deck.id] as const,
  )

  const check = checked.data.slides[0].factCheck
  expect(check.status).toBe('done')

  for (const claim of check.claims as {
    verdict: string
    note: string
    sourceUrl?: string
    text: string
  }[]) {
    expect(['supported', 'unsupported', 'uncertain']).toContain(claim.verdict)
    expect(claim.note.length, `${claim.text} — 판정 근거 설명이 없습니다`).toBeGreaterThan(0)
    if (claim.verdict !== 'uncertain') {
      // The whole point. Nothing is asserted confidently without something the
      // reader can open and disagree with.
      expect(claim.sourceUrl, `"${claim.text}" 를 ${claim.verdict} 로 판정했는데 근거가 없습니다`)
        .toMatch(/^https?:\/\//)
    }
    // Opinions are not verdicts. "our approach is better" has nothing to
    // search for, and a verdict-shaped answer about it is worse than silence.
    expect(claim.text).not.toContain('훨씬 낫다')
  }

  // And it renders, with the source reachable.
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  const panel = page.getByRole('dialog')
  await expect(panel.getByRole('button', { name: '팩트체크' })).toBeVisible({ timeout: 20_000 })

  await page.evaluate(
    async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`, { method: 'DELETE' }),
    [AS_USER, deck.id] as const,
  )
})
