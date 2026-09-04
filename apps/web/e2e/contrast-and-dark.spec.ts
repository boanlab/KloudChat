import { expect, test, type Page } from '@playwright/test'
import { signInAs } from './helpers'

/** WCAG AA contrast (4.5:1 body, 3:1 large) and no invisible text, in both themes, on every route.
 *  Measured against the first opaque background behind each element. */

const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }
const ADMIN = { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' }

const ROUTES = [
  '/',
  '/new/chat',
  '/new/report',
  '/new/slides',
  '/new/image',
  '/projects',
  '/artifacts',
  '/designs',
  '/agents',
  '/skills',
  '/memory',
  '/history',
  '/usage',
  '/connectors',
  '/settings',
  '/settings/preferences',
]

const ADMIN_ROUTES = ['/admin/users', '/admin/usage', '/admin/system', '/admin/governance']

/** Puts the app in one theme and leaves it there. */
async function wear(page: Page, theme: 'light' | 'dark') {
  await page.evaluate((mode) => {
    localStorage.setItem('kchat-theme', mode)
    document.documentElement.classList.toggle('dark', mode === 'dark')
  }, theme)
}

interface Finding {
  route: string
  kind: 'contrast' | 'invisible'
  detail: string
}

async function audit(page: Page, route: string, theme: 'light' | 'dark'): Promise<Finding[]> {
  await page.goto(route)
  await wear(page, theme)
  await page.waitForTimeout(1_400)

  return await page.evaluate(
    ({ route, theme }) => {
      const found: { route: string; kind: 'contrast' | 'invisible'; detail: string }[] = []

      const parse = (colour: string): [number, number, number, number] => {
        const bits = colour.match(/[\d.]+/g)?.map(Number) ?? []
        return [bits[0] ?? 0, bits[1] ?? 0, bits[2] ?? 0, bits[3] ?? 1]
      }
      const channel = (value: number) => {
        const v = value / 255
        return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
      }
      const luminance = ([r, g, b]: number[]) =>
        0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
      const ratio = (a: number[], b: number[]) => {
        const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p)
        return (x + 0.05) / (y + 0.05)
      }

      /** The first opaque background behind this element, or `null` when it is a gradient or picture. */
      const ground = (node: Element): [number, number, number, number] | null => {
        for (let at: Element | null = node; at; at = at.parentElement) {
          const style = getComputedStyle(at)
          if (style.backgroundImage && style.backgroundImage !== 'none') return null
          const colour = parse(style.backgroundColor)
          if (colour[3] > 0.85) return colour
        }
        return parse(getComputedStyle(document.body).backgroundColor)
      }

      const main = document.querySelector('main')
      if (!main) return found

      /** Inside a scaled-down thumbnail: a document on its own paper does not follow the theme. */
      const inPreview = (node: Element) => {
        for (let at: Element | null = node; at && at !== main; at = at.parentElement) {
          const style = getComputedStyle(at)
          if (style.scale && style.scale !== 'none' && Number(style.scale.split(/\s+/)[0]) < 0.95) {
            return true
          }
          if (style.transform && style.transform.startsWith('matrix')) {
            const value = Number(style.transform.split('(')[1]?.split(',')[0])
            if (Number.isFinite(value) && value > 0 && value < 0.95) return true
          }
          if (at.getAttribute('aria-hidden') === 'true') return true
        }
        return false
      }

      for (const node of Array.from(main.querySelectorAll<HTMLElement>('*'))) {
        if (!node.offsetParent) continue
        if (node.children.length) continue
        if (inPreview(node)) continue
        const text = node.innerText?.trim()
        if (!text || text.length < 2) continue

        const style = getComputedStyle(node)
        if (style.visibility === 'hidden' || Number(style.opacity) < 0.4) continue
        const fg = parse(style.color)
        // A see-through colour is a deliberate fade.
        if (fg[3] < 0.7) continue
        const bg = ground(node)
        if (!bg) continue

        const contrast = ratio(fg, bg)
        const size = Number.parseFloat(style.fontSize)
        const bold = Number(style.fontWeight) >= 700
        // WCAG's "large text": 18.66px bold, or 24px.
        const large = size >= 24 || (bold && size >= 18.66)
        const floor = large ? 3 : 4.5

        if (contrast < 1.35) {
          found.push({
            route,
            kind: 'invisible',
            detail: `${theme} · 「${text.slice(0, 26)}」 ${contrast.toFixed(2)}:1`,
          })
        } else if (contrast < floor) {
          found.push({
            route,
            kind: 'contrast',
            detail: `${theme} · 「${text.slice(0, 26)}」 ${contrast.toFixed(2)}:1 (${Math.round(size)}px, ${floor}:1 필요)`,
          })
        }
      }
      return found
    },
    { route, theme },
  )
}

async function sweep(page: Page, label: string, routes: string[], theme: 'light' | 'dark') {
  const all: Finding[] = []
  for (const route of routes) all.push(...(await audit(page, route, theme)))

  console.log(`\n===== ${label} · ${theme === 'dark' ? '어둡게' : '밝게'} =====`)
  const byRoute = new Map<string, Finding[]>()
  for (const one of all) byRoute.set(one.route, [...(byRoute.get(one.route) ?? []), one])
  for (const route of routes) {
    const rows = byRoute.get(route) ?? []
    if (!rows.length) {
      console.log(`· ${route}`)
      continue
    }
    console.log(`✗ ${route} — ${rows.length}건`)
    for (const row of rows.slice(0, 6)) console.log(`    [${row.kind}] ${row.detail}`)
    if (rows.length > 6) console.log(`    … 외 ${rows.length - 6}건`)
  }
  return all
}

test('밝게 · 어둡게 모두 읽힌다 — 사용자 화면', async ({ page }) => {
  test.setTimeout(900_000)
  await signInAs(page, USER.email, USER.password)

  const light = await sweep(page, '사용자', ROUTES, 'light')
  const dark = await sweep(page, '사용자', ROUTES, 'dark')

  expect(
    [...light, ...dark].map((one) => `${one.route} — [${one.kind}] ${one.detail}`),
    'WCAG AA 에 못 미치는 글자',
  ).toEqual([])
})

test('밝게 · 어둡게 모두 읽힌다 — 관리자 화면', async ({ page }) => {
  test.setTimeout(600_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  const light = await sweep(page, '관리자', ADMIN_ROUTES, 'light')
  const dark = await sweep(page, '관리자', ADMIN_ROUTES, 'dark')

  expect(
    [...light, ...dark].map((one) => `${one.route} — [${one.kind}] ${one.detail}`),
    'WCAG AA 에 못 미치는 글자',
  ).toEqual([])
})
