import { expect, test, type ConsoleMessage, type Page } from '@playwright/test'
import { signInAs } from './helpers'

/**
 * Every route in the product, walked as the two real accounts.
 *
 * Not a feature test: nothing here knows what a page is *for*. It asks the
 * three questions that hold for all of them — did it render something a person
 * can act on, did it do so without throwing, and does the role boundary hold —
 * and it names the page that failed rather than a selector, because the point
 * of a sweep is to say where to look next.
 */

const ADMIN = { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' }
const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }

/** Routes every signed-in account may reach. */
const COMMON = [
  '/',
  '/new/chat',
  '/new/report',
  '/new/slides',
  '/new/image',
  '/new/av',
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
  '/settings/access',
]

/** Routes only an administrator may reach. */
const ADMIN_ONLY = [
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

/** Noise the browser makes that is not the app's fault. */
const IGNORED = [
  /favicon/i,
  /ResizeObserver loop/i,
  /Download the React DevTools/i,
  /\[vite\]/i,
  // A cancelled SSE stream when the page navigates away.
  /net::ERR_ABORTED/,
]

interface Report {
  route: string
  errors: string[]
  failedRequests: string[]
  crashed: boolean
  blank: boolean
  headline: string
  rawKeys: string[]
  controls: number
  unnamed: string[]
}

function collect(page: Page, sink: { errors: string[]; failed: string[] }) {
  const onConsole = (m: ConsoleMessage) => {
    if (m.type() !== 'error') return
    const text = m.text()
    if (IGNORED.some((r) => r.test(text))) return
    sink.errors.push(text.slice(0, 300))
  }
  page.on('console', onConsole)
  page.on('pageerror', (e) => sink.errors.push(`pageerror: ${String(e).slice(0, 300)}`))
  page.on('response', (r) => {
    if (r.status() < 400) return
    if (IGNORED.some((re) => re.test(r.url()))) return
    // 401 on a probe the app makes before signing in is expected.
    if (r.status() === 401 && /auth\/me/.test(r.url())) return
    sink.failed.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`)
  })
}

async function visit(page: Page, route: string): Promise<Report> {
  const sink = { errors: [] as string[], failed: [] as string[] }
  collect(page, sink)
  await page.goto(route)
  // The shell is a Suspense boundary; wait for the spinner to go before
  // judging emptiness.
  await page
    .locator('[data-testid="spinner"], [role="status"]')
    .first()
    .waitFor({ state: 'hidden', timeout: 10_000 })
    .catch(() => undefined)
  await page.waitForTimeout(1_200)

  const main = page.locator('main').first()
  const text = (await main.innerText().catch(() => '')) || ''
  const crashed = /화면을 그리지 못했습니다|render failed|Something went wrong/i.test(text)

  // Raw translation keys that leaked to the screen, e.g. `nav.projects`.
  const rawKeys = [...text.matchAll(/\b[a-z][a-zA-Z]+(?:\.[a-z][a-zA-Z]+){1,3}\b/g)]
    .map((m) => m[0])
    .filter((k) => !/\.(com|net|org|zone|io|html|json|md|pdf|docx|png|jpg|ts|tsx|py)$/.test(k))
    .filter((k) => !/^(e\.g|i\.e|localhost|kchat|www)\./.test(k))

  const buttons = main.getByRole('button')
  const n = await buttons.count()
  const unnamed: string[] = []
  for (let i = 0; i < Math.min(n, 60); i++) {
    const b = buttons.nth(i)
    if (!(await b.isVisible().catch(() => false))) continue
    const name =
      (await b.getAttribute('aria-label')) || (await b.innerText().catch(() => '')) || ''
    if (!name.trim()) unnamed.push((await b.getAttribute('class'))?.slice(0, 60) ?? '?')
  }

  const headline =
    (await page.locator('h1, h2').first().innerText().catch(() => '')) || text.split('\n')[0] || ''

  page.removeAllListeners('console')
  page.removeAllListeners('pageerror')
  page.removeAllListeners('response')

  return {
    route,
    errors: sink.errors,
    failedRequests: sink.failed,
    crashed,
    blank: text.trim().length < 20,
    headline: headline.slice(0, 80),
    rawKeys: [...new Set(rawKeys)],
    controls: n,
    unnamed,
  }
}

function summarise(label: string, reports: Report[]) {
  const bad = reports.filter(
    (r) => r.crashed || r.blank || r.errors.length || r.failedRequests.length || r.unnamed.length,
  )
  console.log(`\n===== ${label} — ${reports.length}개 경로, 문제 ${bad.length}건 =====`)
  for (const r of reports) {
    const flags = [
      r.crashed && 'CRASH',
      r.blank && 'BLANK',
      r.errors.length && `console:${r.errors.length}`,
      r.failedRequests.length && `http:${r.failedRequests.length}`,
      r.unnamed.length && `unnamed:${r.unnamed.length}`,
    ].filter(Boolean)
    console.log(
      `${flags.length ? '✗' : '·'} ${r.route.padEnd(26)} [${String(r.controls).padStart(3)} ctl] ${r.headline}` +
        (flags.length ? `  <<< ${flags.join(' ')}` : ''),
    )
    for (const e of r.errors.slice(0, 4)) console.log(`      console: ${e}`)
    for (const f of [...new Set(r.failedRequests)].slice(0, 6)) console.log(`      http: ${f}`)
    if (r.unnamed.length) console.log(`      이름 없는 버튼: ${r.unnamed.slice(0, 4).join(' | ')}`)
    if (r.rawKeys.length) console.log(`      점찍힌 문자열: ${r.rawKeys.slice(0, 6).join(', ')}`)
  }
  return bad
}

test('모든 화면 — 관리자', async ({ page }) => {
  test.setTimeout(600_000)
  await signInAs(page, ADMIN.email, ADMIN.password)
  const reports: Report[] = []
  for (const route of [...COMMON, ...ADMIN_ONLY]) reports.push(await visit(page, route))
  const bad = summarise('관리자', reports)
  const fatal = bad.filter((r) => r.crashed || r.blank)
  expect(fatal.map((r) => r.route), '빈 화면이거나 렌더에 실패한 경로').toEqual([])
})

test('모든 화면 — 일반 사용자', async ({ page }) => {
  test.setTimeout(600_000)
  await signInAs(page, USER.email, USER.password)
  const reports: Report[] = []
  for (const route of COMMON) reports.push(await visit(page, route))
  const bad = summarise('일반 사용자', reports)
  const fatal = bad.filter((r) => r.crashed || r.blank)
  expect(fatal.map((r) => r.route), '빈 화면이거나 렌더에 실패한 경로').toEqual([])
})

test('일반 사용자는 관리자 화면에 닿지 못한다', async ({ page }) => {
  test.setTimeout(300_000)
  await signInAs(page, USER.email, USER.password)
  const leaked: string[] = []
  for (const route of ADMIN_ONLY) {
    await page.goto(route)
    await page.waitForTimeout(800)
    const denied = page
      .getByText(/이 페이지에 접근할 수 없습니다|접근 제한|Access denied|관리자 권한이 필요/i)
      .first()
    const ok = await denied.isVisible().catch(() => false)
    // Redirected home is also a correct answer.
    const home = !page.url().includes('/admin')
    if (!ok && !home) leaked.push(`${route} → ${(await page.locator('main').innerText()).slice(0, 60)}`)
  }
  console.log('\n===== 역할 경계 =====')
  console.log(leaked.length ? leaked.join('\n') : '모든 관리자 경로가 막혔습니다')
  expect(leaked, '일반 사용자에게 열린 관리자 경로').toEqual([])
})
