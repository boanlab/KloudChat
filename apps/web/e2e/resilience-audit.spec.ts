import { mkdir, writeFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Round three: what the screen says when it does not yet know, and what it
 * says when the answer never comes.
 *
 * Rounds one and two looked at a screen that had loaded and a keyboard that
 * worked. This one takes those away — it holds the API back, and then breaks
 * it — and reads what is left on screen. An empty state shown while the list
 * is still in flight is not a neutral placeholder; it is the app telling
 * somebody their work is gone.
 *
 * Discovery, like the others: writes `audit/resilience-audit.json`, never
 * fails the run.
 */

interface Defect {
  rule: string
  subject: string
  where: string
  hurts: string
}

/** Screens that render a list, and the call that fills it. */
const LISTS: [route: string, api: RegExp, where: string][] = [
  ['/artifacts', /\/api\/artifacts(\?|$)/, '아티팩트'],
  ['/memory', /\/api\/memory(\?|$)/, '메모리'],
  ['/projects', /\/api\/projects(\?|$)/, '프로젝트'],
  ['/agents', /\/api\/agents(\?|$)/, '에이전트'],
  ['/skills', /\/api\/skills(\?|$)/, '스킬'],
  ['/history', /\/api\/sessions(\?|$)/, '대화 기록'],
  ['/connectors', /\/api\/connectors(\?|$)/, '커넥터'],
]

/** Anything that reads as "there is nothing here". */
const SAYS_EMPTY = /없습니다|비어 있습니다|아직 .*없/

/** Anything that reads as "still working on it". */
const SAYS_BUSY = /불러오는 중|로딩|잠시|기다/

test('회복력 감사 — 느릴 때와 실패할 때', async ({ page }) => {
  test.setTimeout(1_200_000)
  const defects: Defect[] = []
  let checks = 0
  const note = (rule: string, subject: string, where: string, hurts: string) =>
    defects.push({ rule, subject, where, hurts })

  await signIn(page)

  /* ── R17: an empty screen and a slow one are different things ────────
     Every list here renders "nothing yet" from `array.length === 0`, which is
     also what an unanswered request looks like. Held for two seconds, the
     screen should say it is working — not that the work is gone. */
  for (const [route, api, where] of LISTS) {
    await page.route(api, async (r) => {
      await new Promise((done) => setTimeout(done, 2500))
      // The page may have moved on by the time this wakes; letting that throw
      // would fail the audit for a reason that is not about the app.
      await r.continue().catch(() => {})
    })
    await page.goto(route)
    await page.waitForTimeout(900) // mid-flight, on purpose

    const text = await page.evaluate(() => document.body.innerText)
    checks++
    if (SAYS_EMPTY.test(text) && !SAYS_BUSY.test(text)) {
      note('loading-honesty', where, route, '아직 오지 않은 것을 없다고 말한다')
    }
    // Let the held request land *before* dropping the handler — unrouting
    // underneath a sleeping one leaves it with nothing to continue into.
    await page.waitForTimeout(2200)
    await page.unroute(api)
  }

  /* ── R16: a failure has to be sayable and retryable ──────────────────
     Not "did it log an error" — whether the person looking at it is told
     something happened, in words, with a way to try again. */
  const FAILURES: [route: string, api: RegExp, act: (p: Page) => Promise<void>, where: string][] = [
    [
      '/skills',
      /\/api\/skills$/,
      async (p) => {
        await p.getByRole('button', { name: '새 스킬' }).first().click()
        await p.getByRole('dialog').getByLabel(/이름/).first().fill('실패 확인용')
        await p.getByRole('dialog').getByRole('button', { name: /^저장$|^만들기$/ }).last().click()
      },
      '스킬 만들기',
    ],
    [
      '/agents',
      /\/api\/agents$/,
      async (p) => {
        await p.getByRole('button', { name: '새 에이전트' }).first().click()
        await p.getByRole('dialog').getByLabel(/이름/).first().fill('실패 확인용')
        await p.getByRole('dialog').getByRole('button', { name: /^저장$/ }).last().click()
      },
      '에이전트 만들기',
    ],
    [
      '/memory',
      /\/api\/memory$/,
      async (p) => {
        await p.getByRole('button', { name: '새 메모리' }).first().click()
        await p.getByRole('dialog').getByLabel(/이름/).first().fill('실패 확인용')
        await p.getByRole('dialog').getByRole('button', { name: /^저장$|^추가$/ }).last().click()
      },
      '메모리 만들기',
    ],
  ]

  for (const [route, api, act, where] of FAILURES) {
    await page.goto(route)
    await page.waitForTimeout(600)
    await page.route(api, async (r) => {
      if (r.request().method() === 'GET') return r.continue()
      await r.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'upstream exploded' }),
      })
    })
    await act(page).catch(() => {})
    await page.waitForTimeout(1200)

    // Scoped to the form. Read off the whole page this matched a tooltip and a
    // nav label, and reported a silent failure as handled — which is how the
    // skills form kept swallowing rejections through three rounds of audits.
    const text = await page
      .getByRole('dialog')
      .innerText()
      .catch(() => page.evaluate(() => document.body.innerText))
    checks++
    // Something on screen has to admit it failed.
    const said = /실패|오류|되지 않|못했|없습니다/.test(text)
    if (!said) note('error-visible', where, route, '실패했는데 화면은 아무 말도 하지 않는다')

    checks++
    // And it must not be the wire talking: a status code or a stack is not a
    // sentence anybody can act on.
    if (/upstream exploded|500|Internal Server Error|\{"detail"/.test(text)) {
      note('error-readable', where, route, '서버 원문이 그대로 사용자에게 나온다')
    }
    await page.unroute(api)
  }

  /* ── R15: menus and the keyboard ─────────────────────────────────────
     `role="menu"` is a promise about arrow keys. These are opened with the
     keyboard by anyone who does not reach for the mouse, and then there is
     nowhere to go. */
  const MENUS: [route: string, open: string, where: string][] = [
    ['/new/chat', '모델', '모델 선택기'],
  ]
  for (const [route, , where] of MENUS) {
    await page.goto(route)
    await page.waitForTimeout(600)
    const trigger = page.locator('button').filter({ hasText: /·/ }).last()
    if ((await trigger.count()) === 0) continue
    await trigger.click()
    const menu = page.getByRole('menu')
    if ((await menu.count()) === 0) continue

    // Focus should be in the menu, or reachable with one ArrowDown.
    checks++
    await page.keyboard.press('ArrowDown')
    const inMenu = await page.evaluate(() => {
      const m = document.querySelector('[role="menu"]')
      return !!m && !!document.activeElement && m.contains(document.activeElement)
    })
    if (!inMenu) note('menu-keyboard', where, route, '방향키로 항목을 고를 수 없다')

    await page.keyboard.press('Escape')
    await page.waitForTimeout(200)
    checks++
    const back = await page.evaluate(
      () => (document.activeElement as HTMLElement | null)?.tagName === 'BUTTON',
    )
    if (!back) note('menu-restore', where, route, '닫으면 초점이 어디로 갔는지 알 수 없다')
  }

  const byRule = defects.reduce<Record<string, number>>((acc, d) => {
    acc[d.rule] = (acc[d.rule] ?? 0) + 1
    return acc
  }, {})
  await mkdir('audit', { recursive: true })
  await writeFile(
    'audit/resilience-audit.json',
    JSON.stringify({ checks, defects: defects.length, byRule, defectList: defects }, null, 2),
  )
  console.log(`checks=${checks} defects=${defects.length}`)
  console.log(JSON.stringify(byRule, null, 2))
  expect(checks).toBeGreaterThan(0)
})
