import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/**
 * 한글은 공백에서 끊긴다.
 *
 * The browser breaks a Korean line at any character, which in a Korean-first
 * interface reads as a rendering fault wherever a label is narrower than its
 * text: a table header came out as 「마지막 활 / 동」, a status chip as 「승인 대
 * / 기」, a deck cover as 「… 교육 의무 / 화 추진 전략」. The rule is one line of
 * inherited CSS on `body`, so this checks it is *there* — on the element and
 * on a few descendants that had the visible failures — rather than trying to
 * measure where lines fall.
 */

const ADMIN = { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' }

test('앱 전체가 한글을 단어 중간에서 끊지 않는다', async ({ page }) => {
  test.setTimeout(180_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  await expect(page.locator('body')).toHaveCSS('word-break', 'keep-all')

  // 관리자 표 — 「마지막 활 / 동」이 나왔던 자리.
  await page.goto('/admin/users')
  await expect(page.getByRole('heading', { name: /사용자/ })).toBeVisible({ timeout: 20_000 })
  const inherited = await page.evaluate(() => {
    const seen: Record<string, string> = {}
    for (const selector of ['th', 'h1', 'p', 'span', 'td']) {
      const node = document.querySelector(`main ${selector}`)
      if (node) seen[selector] = getComputedStyle(node).wordBreak
    }
    return seen
  })
  for (const [selector, value] of Object.entries(inherited)) {
    expect(value, `${selector} 가 keep-all 을 물려받지 못했습니다`).toBe('keep-all')
  }

  // 긴 토큰은 여전히 접힌다 — keep-all 만 두면 URL 이 상자 밖으로 나간다.
  const wraps = await page.evaluate(() => {
    const probe = document.createElement('div')
    probe.style.width = '80px'
    probe.textContent = 'https://example.com/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    document.body.appendChild(probe)
    const overflowed = probe.scrollWidth > probe.clientWidth + 2
    probe.remove()
    return !overflowed
  })
  expect(wraps, '긴 URL 이 상자를 넘쳤습니다').toBe(true)
})
