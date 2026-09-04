import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/** `word-break: keep-all` on `body` is inherited everywhere, and long tokens still wrap. */

const ADMIN = { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' }

test('앱 전체가 한글을 단어 중간에서 끊지 않는다', async ({ page }) => {
  test.setTimeout(180_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  await expect(page.locator('body')).toHaveCSS('word-break', 'keep-all')

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

  // Long tokens still wrap: keep-all alone lets a URL overflow.
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
