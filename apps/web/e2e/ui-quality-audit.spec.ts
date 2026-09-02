import { expect, test, type Page } from '@playwright/test'
import { signInAs } from './helpers'

/**
 * 상용 수준 UI 가 어기지 않는 것들을, 화면마다 기계로 잰다.
 *
 * Not taste. Every rule here is something a person can see going wrong and a
 * machine can measure: text clipped by its own box, a row of cards that do not
 * line up, a page that scrolls sideways, a control too small to hit, a label
 * that overlaps the thing beside it. Judgement calls — spacing rhythm, colour,
 * where the emphasis belongs — are not in here, because a test that encodes
 * taste fails on every redesign and teaches nobody anything.
 *
 * The audit reports every screen and asserts only on the defects, so one bad
 * page names itself instead of hiding in a pass.
 */

const ADMIN = { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' }
const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }

const USER_ROUTES = [
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
  '/agent-setup',
  '/api-setup',
  '/settings',
  '/settings/preferences',
  '/settings/keys',
]

const ADMIN_ROUTES = [
  '/admin/users',
  '/admin/usage',
  '/admin/system',
  '/admin/system/routing',
  '/admin/system/features',
  '/admin/system/templates',
  '/admin/system/branding',
  '/admin/system/mail',
  '/admin/governance',
]

interface Defect {
  kind: string
  detail: string
}

async function audit(page: Page, route: string): Promise<Defect[]> {
  await page.goto(route)
  await page.waitForTimeout(1_600)

  return await page.evaluate(() => {
    const found: { kind: string; detail: string }[] = []
    const say = (node: Element) => {
      const text = (node as HTMLElement).innerText?.trim().replace(/\s+/g, ' ').slice(0, 40)
      return text || node.className?.toString().slice(0, 40) || node.tagName
    }

    // ── 가로 스크롤 ────────────────────────────────────────────────
    // A page that scrolls sideways is a layout that did not fit, every time.
    if (document.documentElement.scrollWidth > window.innerWidth + 2) {
      found.push({
        kind: 'page-overflow',
        detail: `문서 너비 ${document.documentElement.scrollWidth} > 창 ${window.innerWidth}`,
      })
    }

    const main = document.querySelector('main')
    if (!main) return found

    // A thumbnail is a whole document shrunk into a card, and cropping it is
    // the entire point — so nothing inside one counts as clipped or as too
    // small to press. Identified by the scale on it rather than by a class
    // name, because that is what makes it a preview.
    const shrunk = (style: CSSStyleDeclaration): boolean => {
      // Two spellings of the same thing: `transform: matrix(...)` and the
      // standalone `scale` property Tailwind v4 emits for `scale-[0.45]`.
      // Reading only the first meant the artifact thumbnails — which are whole
      // documents at 45% — were audited as if they were page content.
      if (style.transform && style.transform.startsWith('matrix')) {
        const value = Number(style.transform.split('(')[1]?.split(',')[0])
        if (Number.isFinite(value) && value > 0 && value < 0.95) return true
      }
      if (style.scale && style.scale !== 'none') {
        const value = Number(style.scale.split(/\s+/)[0])
        if (Number.isFinite(value) && value > 0 && value < 0.95) return true
      }
      return false
    }

    const inPreview = (node: Element) => {
      for (let at: Element | null = node; at && at !== main; at = at.parentElement) {
        if (shrunk(getComputedStyle(at))) return true
        if (at.getAttribute('aria-hidden') === 'true') return true
      }
      return false
    }

    // ── 잘린 글자 ──────────────────────────────────────────────────
    // Real clipping only: the box hides overflow, and the text is wider or
    // taller than the box by more than a rounding error. Scroll containers are
    // excluded — a list that scrolls is not a list that is clipped.
    for (const node of Array.from(main.querySelectorAll<HTMLElement>('h1,h2,h3,p,span,button,th,td,label,a'))) {
      if (!node.offsetParent) continue
      if (node.children.length) continue
      if (inPreview(node)) continue
      const style = getComputedStyle(node)
      if (style.overflow === 'visible' && style.overflowX === 'visible') continue
      if (style.overflowY === 'auto' || style.overflowY === 'scroll') continue
      // An ellipsis is a deliberate truncation, not a defect.
      if (style.textOverflow === 'ellipsis') continue
      if (node.scrollWidth > node.clientWidth + 2 || node.scrollHeight > node.clientHeight + 2) {
        found.push({ kind: 'clipped-text', detail: say(node) })
      }
    }

    // ── 손가락으로 누를 수 없는 것 ───────────────────────────────────
    // 24px is the WCAG 2.2 minimum for a target that has no spacing exemption.
    //
    // Measured on the *target*, not on the painted control. A 16px checkbox
    // inside a 32px `<label>` is a 32px target, because pressing anywhere in
    // the label toggles it — the same split `Switch` already makes between its
    // 44×36 button and the 36×20 track it draws. Reading the input alone
    // reported every list in the product as broken and named nothing.
    const target = (node: HTMLElement): DOMRect => {
      let box = node.getBoundingClientRect()
      if (node.tagName !== 'INPUT') return box
      const label = node.closest('label')
      if (label) {
        const outer = label.getBoundingClientRect()
        if (outer.width >= box.width && outer.height >= box.height) box = outer
      }
      return box
    }

    // WCAG 2.2 exempts a target set in a line of running text — its height is
    // the line-height of the sentence around it, and growing it would break the
    // paragraph. 「…따로 집계됩니다. 사용량」 is that case, not a small button.
    const inSentence = (node: HTMLElement): boolean => {
      if (node.tagName !== 'A') return false
      if (!getComputedStyle(node).display.startsWith('inline')) return false
      const parent = node.parentElement
      if (!parent) return false
      return Array.from(parent.childNodes).some(
        (child) => child.nodeType === Node.TEXT_NODE && (child.textContent ?? '').trim().length > 0,
      )
    }

    for (const node of Array.from(main.querySelectorAll<HTMLElement>('button,a[href],[role="tab"],input[type="checkbox"]'))) {
      if (!node.offsetParent) continue
      if (inPreview(node)) continue
      if (inSentence(node)) continue
      const box = target(node)
      if (box.width === 0 || box.height === 0) continue
      if (box.width < 24 || box.height < 24) {
        found.push({
          kind: 'tap-size',
          detail: `${say(node)} — ${Math.round(box.width)}×${Math.round(box.height)}`,
        })
      }
    }

    // ── 어긋난 격자 ─────────────────────────────────────────────────
    // Cards in one row of a grid have to end at the same line. Ragged rows are
    // the single most common reason a competent layout still looks unfinished.
    for (const grid of Array.from(main.querySelectorAll<HTMLElement>('[class*="grid"]'))) {
      const cells = Array.from(grid.children) as HTMLElement[]
      if (cells.length < 2) continue
      const rows = new Map<number, HTMLElement[]>()
      for (const cell of cells) {
        const box = cell.getBoundingClientRect()
        if (!box.height) continue
        const key = Math.round(box.top / 8)
        rows.set(key, [...(rows.get(key) ?? []), cell])
      }
      for (const row of rows.values()) {
        if (row.length < 2) continue
        const heights = row.map((cell) => cell.getBoundingClientRect().height)
        const spread = Math.max(...heights) - Math.min(...heights)
        if (spread > 12) {
          found.push({
            kind: 'ragged-row',
            detail: `${say(row[0])} … 높이 차 ${Math.round(spread)}px (${row.length}칸)`,
          })
        }
      }
    }

    return found
  })
}

async function sweep(page: Page, label: string, routes: string[]) {
  const table = new Map<string, Defect[]>()
  for (const route of routes) table.set(route, await audit(page, route))

  console.log(`\n===== ${label} =====`)
  const tally = new Map<string, number>()
  for (const [route, defects] of table) {
    if (!defects.length) {
      console.log(`· ${route}`)
      continue
    }
    console.log(`✗ ${route} — ${defects.length}건`)
    for (const defect of defects.slice(0, 8)) {
      console.log(`    [${defect.kind}] ${defect.detail}`)
      tally.set(defect.kind, (tally.get(defect.kind) ?? 0) + 1)
    }
    if (defects.length > 8) console.log(`    … 외 ${defects.length - 8}건`)
  }
  console.log('\n종류별 합계: ' + JSON.stringify(Object.fromEntries(tally), null, 0))
  return table
}

test('일반 사용자 화면의 레이아웃 품질', async ({ page }) => {
  test.setTimeout(600_000)
  await signInAs(page, USER.email, USER.password)
  const table = await sweep(page, '일반 사용자', USER_ROUTES)

  const overflowing = [...table].filter(([, d]) => d.some((x) => x.kind === 'page-overflow'))
  expect(overflowing.map(([route]) => route), '가로로 넘치는 화면').toEqual([])

  const clipped = [...table].flatMap(([route, d]) =>
    d.filter((x) => x.kind === 'clipped-text').map((x) => `${route} — ${x.detail}`),
  )
  expect(clipped, '글자가 상자에 잘린 곳').toEqual([])

  const small = [...table].flatMap(([route, d]) =>
    d.filter((x) => x.kind === 'tap-size').map((x) => `${route} — ${x.detail}`),
  )
  expect(small, '24px 보다 작은 조작 대상').toEqual([])
})

/**
 * 아이디가 있어야 열리는 화면들.
 *
 * `/projects/:id` and `/share/:token` cannot be listed beside the fixed routes
 * because neither exists until something is made — and a screen that is never
 * audited is where the defects collect. The share page especially: it is the
 * one screen people who are not users of this instance ever see.
 */
test('아이디로 여는 화면의 레이아웃 품질', async ({ page }) => {
  test.setTimeout(600_000)
  await signInAs(page, USER.email, USER.password)

  const defects: string[] = []
  const check = async (route: string, label: string) => {
    for (const defect of await audit(page, route)) {
      defects.push(`${label} — [${defect.kind}] ${defect.detail}`)
    }
  }

  // 프로젝트 상세: 목록에서 첫 칸을 연다.
  await page.goto('/projects')
  await page.waitForTimeout(1_200)
  const project = page.locator('main a[href^="/projects/"]').first()
  const href = await project.getAttribute('href').catch(() => null)
  if (href) await check(href, '프로젝트 상세')

  // 공유 화면: 문서 하나를 공유해 그 링크를 연다.
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^보고서/ }).click()
  await page.waitForTimeout(1_200)
  const open = page.getByText('원본 작업 열기').first()
  if (await open.isVisible().catch(() => false)) {
    await open.click()
    await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
    await page.getByRole('button', { name: '공유' }).first().click()
    await page.waitForTimeout(1_500)
    const link = await page
      .locator('input[readonly], [data-share-url]')
      .first()
      .inputValue()
      .catch(() => '')
    const token = link.match(/\/share\/([A-Za-z0-9_-]+)/)?.[1]
    await page.keyboard.press('Escape')
    if (token) await check(`/share/${token}`, '공유 화면')
    else console.log('공유 링크를 찾지 못해 건너뜁니다')
  }

  console.log('\n===== 아이디로 여는 화면 =====')
  console.log(defects.length ? defects.join('\n') : '문제 없음')
  const real = defects.filter((d) => !d.includes('ragged-row'))
  expect(real, '아이디로 여는 화면의 결함').toEqual([])
})

/**
 * 결과물 화면도 화면이다.
 *
 * The sweep above walks the routes a person navigates to, and the document and
 * deck panels are on none of them — they open inside `/s/:id`. So the screen
 * people spend the longest looking at was the one screen never audited, and it
 * was carrying a real defect: a `timeline` slide's left-hand labels were laid
 * out at a date's width, so 「개편 방향 확정」 wrapped inside a row that clips
 * and came out as 「개편 방향 확」.
 */
test('문서와 덱 패널의 레이아웃 품질', async ({ page }) => {
  test.setTimeout(600_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  const defects: string[] = []
  for (const [tab, label] of [
    [/^슬라이드/, '덱'],
    [/^보고서/, '문서'],
  ] as [RegExp, string][]) {
    await page.goto('/artifacts')
    const chip = page.getByRole('tab', { name: tab })
    if (!(await chip.isVisible().catch(() => false))) continue
    await chip.click()
    await page.waitForTimeout(1_200)
    const open = page.getByText('원본 작업 열기').first()
    if (!(await open.isVisible().catch(() => false))) continue
    await open.click()
    await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
    const panel = page.locator('[data-panel="artifact"]')
    await expect(panel).toBeVisible({ timeout: 30_000 })
    await expect(page.getByLabel('중지')).toBeHidden({ timeout: 180_000 })
    await page.waitForTimeout(2_500)

    // 장을 넘겨 가며 잰다 — 잘림은 대개 한 장에만 있다.
    for (let n = 0; n < 8; n++) {
      const clipped = await panel.evaluate((root) => {
        const bad: string[] = []
        for (const node of Array.from(root.querySelectorAll<HTMLElement>('div,span,p,h1,h2,h3'))) {
          if (!node.offsetParent) continue
          if (node.children.length) continue
          const text = node.innerText?.trim()
          if (!text) continue
          const style = getComputedStyle(node)
          if (style.overflow === 'visible' && style.overflowY === 'visible') continue
          if (style.overflowY === 'auto' || style.overflowY === 'scroll') continue
          if (style.textOverflow === 'ellipsis') continue
          // 세로로 잘린 것만. 가로 잘림은 말줄임이나 스크롤이 흔하다.
          if (node.scrollHeight > node.clientHeight + 3) {
            bad.push(`${text.slice(0, 30)} (${node.clientHeight}px 안에 ${node.scrollHeight}px)`)
          }
        }
        return bad
      })
      for (const one of clipped) defects.push(`${label} ${n + 1}장 — ${one}`)

      const next = page.getByRole('button', { name: /다음/ }).first()
      if (!(await next.isVisible().catch(() => false))) break
      if (await next.isDisabled().catch(() => false)) break
      await next.click()
      await page.waitForTimeout(700)
    }
  }

  console.log('\n===== 결과물 화면 =====')
  console.log(defects.length ? defects.join('\n') : '잘린 곳 없음')
  expect([...new Set(defects)], '결과물 화면에서 잘린 글자').toEqual([])
})

test('관리자 화면의 레이아웃 품질', async ({ page }) => {
  test.setTimeout(600_000)
  await signInAs(page, ADMIN.email, ADMIN.password)
  const table = await sweep(page, '관리자', ADMIN_ROUTES)

  const overflowing = [...table].filter(([, d]) => d.some((x) => x.kind === 'page-overflow'))
  expect(overflowing.map(([route]) => route), '가로로 넘치는 화면').toEqual([])

  const clipped = [...table].flatMap(([route, d]) =>
    d.filter((x) => x.kind === 'clipped-text').map((x) => `${route} — ${x.detail}`),
  )
  expect(clipped, '글자가 상자에 잘린 곳').toEqual([])
})
