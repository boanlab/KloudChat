import { test, expect, type Page } from '@playwright/test'

/**
 * Looks for placeholders that reached the screen unsubstituted.
 *
 * `t('배정 {n}')` is translated first and then filled in with
 * `.replace('{n}', …)`. Misspell the placeholder in the English entry and the
 * substitution misses, leaving a literal `{n}` on screen — which the type
 * checker cannot catch.
 */

const ADMIN = { email: 'admin@example.com', password: 'KloudChat-Admin-1234' }
const PLACEHOLDER = /\{(n|name|email|date|when|title|kind|style|list|low|high|in|out|shown|total|done|words|limit|pct|preview|days|reqs|credits)\}/

const ROUTES = [
  '/', '/new/chat', '/new/report', '/new/slides', '/new/image', '/new/av',
  '/projects', '/artifacts', '/agents', '/skills', '/memory', '/history',
  '/usage', '/connectors', '/agent-setup', '/settings', '/settings/preferences',
  '/settings/keys', '/settings/system', '/admin/users', '/admin/usage',
  '/admin/system', '/admin/governance',
]

async function scan(page: Page, where: string) {
  await page.waitForTimeout(500)
  return page.evaluate(
    ({ where, rx }) => {
      const re = new RegExp(rx)
      const out: { where: string; text: string }[] = []
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const text = (n.textContent || '').trim()
        if (text && re.test(text)) out.push({ where, text })
      }
      for (const el of Array.from(document.querySelectorAll('[aria-label],[placeholder],[title]'))) {
        for (const a of ['aria-label', 'placeholder', 'title']) {
          const v = (el.getAttribute(a) || '').trim()
          if (v && re.test(v)) out.push({ where, text: `[${a}] ${v}` })
        }
      }
      return out
    },
    { where, rx: PLACEHOLDER.source },
  )
}

for (const lang of ['ko', 'en'] as const) {
  test(`${lang}: 치환되지 않은 자리표시자`, async ({ page }) => {
    await page.addInitScript((l) => localStorage.setItem('kchat-lang', l), lang)
    await page.goto('/')
    await page.waitForTimeout(1200)
    await page.getByLabel(/^(Email|이메일)$/).fill(ADMIN.email)
    await page.getByLabel(/^(Password|비밀번호)$/).fill(ADMIN.password)
    await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/auth/login')),
      page.locator('form').getByRole('button').last().click(),
    ])
    await page.waitForTimeout(1800)

    const found: { where: string; text: string }[] = []
    for (const path of ROUTES) {
      await page.goto(path)
      found.push(...(await scan(page, path)))
    }
    console.log(`\n=== ${lang}: ${found.length}건 ===`)
    for (const f of found) console.log(`  [${f.where}] ${f.text}`)
    expect(found, `${found.length}건`).toEqual([])
  })
}
