import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/** At phone width every key screen fits: nothing slides the page sideways, no text is
 *  squeezed into a column a few characters wide, and every page menu can be reached.
 *  Read-only: it opens pages and tabs, never saves. */

const PHONE = { width: 390, height: 844 }

const PAGES = [
  '/',
  '/new/report',
  '/projects',
  '/artifacts',
  '/history',
  '/usage',
  '/customize?tab=agents',
  '/customize?tab=connectors',
  '/settings',
  '/settings/memory',
  '/settings/keys',
  '/admin/users',
  '/admin/usage',
  '/admin/performance',
  '/admin/performance/host',
  '/admin/performance/models',
  '/admin/system',
  '/admin/system/models',
  '/admin/system/routing',
  '/admin/system/features',
  '/admin/system/branding',
  '/admin/system/mail',
  '/admin/system/signup',
  '/admin/system/connectors',
  '/admin/publications',
]

/** What does not fit, in words; empty when the screen fits. */
function misfits(page: Page) {
  return page.evaluate(() => {
    const W = window.innerWidth
    const found: string[] = []
    const name = (el: Element) =>
      `${el.tagName.toLowerCase()} "${(el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40)}"`
    const shown = (el: Element) => {
      const s = getComputedStyle(el)
      const r = el.getBoundingClientRect()
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 1 && r.height > 1 &&
        !el.closest('[aria-hidden="true"], [inert], .sr-only')
    }
    const doc = document.documentElement
    if (doc.scrollWidth > doc.clientWidth + 1) found.push(`문서가 옆으로 ${doc.scrollWidth - doc.clientWidth}px 넘친다`)
    for (const el of document.querySelectorAll('main *')) {
      if (!shown(el)) continue
      const s = getComputedStyle(el)
      const r = el.getBoundingClientRect()
      // The page's own scroller sliding sideways: a wide table or code block scrolls in its own box.
      if (['auto', 'scroll'].includes(s.overflowX) && el.scrollWidth > el.clientWidth + 1 && r.width >= W * 0.8 &&
        !el.matches('[role=tablist], nav, pre, table') && !el.closest('pre, code') &&
        !el.querySelector(':scope > table, :scope > pre')) {
        found.push(`${name(el)} 이(가) 옆으로 ${el.scrollWidth - el.clientWidth}px 넘친다`)
      }
      // A few characters per line.
      const own = Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? '').trim().length > 2)
      if (own && r.width < W * 0.36) {
        const line = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.4
        const lines = Math.round(r.height / line)
        // Narrower than a third of the screen because a row beside it takes the room.
        let crowded = r.width < 48
        for (let p = el.parentElement, k = 0; p && k < 3 && !crowded; p = p.parentElement, k++) {
          const ps = getComputedStyle(p)
          const row = (ps.display.includes('flex') && !ps.flexDirection.startsWith('column')) || ps.display.includes('grid')
          if (row && p.getBoundingClientRect().width > r.width * 1.8) crowded = true
        }
        if (crowded && lines > 3) found.push(`${name(el)} 이(가) ${Math.round(r.width)}px 폭에 ${lines}줄로 끼어 있다`)
      }
    }
    // A page menu cut off with no way to scroll to the rest.
    for (const bar of document.querySelectorAll('main [role=tablist], main nav')) {
      if (!shown(bar)) continue
      const right = Math.min(bar.getBoundingClientRect().right, W) + 1
      const cut = Array.from(bar.querySelectorAll('a, button, [role=tab]'))
        .some((item) => shown(item) && item.getBoundingClientRect().right > right)
      if (cut && !['auto', 'scroll'].includes(getComputedStyle(bar).overflowX)) found.push(`${name(bar)} 메뉴가 잘린다`)
    }
    return found
  })
}

test('휴대폰 폭에서 주요 화면이 옆으로 넘치거나 글자가 끼지 않는다', { tag: '@phone' }, async ({ page }) => {
  test.setTimeout(300_000)
  await page.setViewportSize(PHONE)
  await signIn(page)

  const problems: string[] = []
  for (const path of PAGES) {
    await page.goto(path)
    await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {})
    await expect(page.getByRole('button', { name: '사이드바 토글' })).toBeVisible({ timeout: 15_000 })
    for (const p of await misfits(page)) problems.push(`${path}: ${p}`)

    // Every tab of the page, too.
    const tabs = page.locator('main [role=tab]')
    for (let i = 0; i < (await tabs.count()); i++) {
      const tab = tabs.nth(i)
      if (!(await tab.isVisible()) || (await tab.getAttribute('aria-selected')) === 'true') continue
      const label = (await tab.innerText()).trim()
      await tab.click()
      await page.waitForTimeout(400)
      for (const p of await misfits(page)) problems.push(`${path} › ${label}: ${p}`)
    }
  }
  expect(problems).toEqual([])
})
