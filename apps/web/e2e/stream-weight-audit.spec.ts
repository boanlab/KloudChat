import { mkdir, writeFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Round five: the answer is cut off, and the first load is paid for.
 *
 * Round four broke the list endpoints. This one breaks the *stream* — the one
 * request that is not a request but a conversation — and then measures what a
 * cold visit costs somebody on campus wifi.
 *
 * A dropped stream is the failure this app is most likely to meet in the
 * building it runs in: a laptop lid half-closed, a lecture-hall access point
 * handing over, a VPN reconnecting. What must not happen is the turn sitting
 * there pretending to think.
 *
 * Discovery: writes `audit/stream-weight-audit.json`, never fails the run.
 */

interface Defect {
  rule: string
  subject: string
  hurts: string
}

test('스트림·적재 감사 — 끊긴 답변과 첫 화면 비용', async ({ page }) => {
  test.setTimeout(900_000)
  const defects: Defect[] = []
  let checks = 0
  const note = (rule: string, subject: string, hurts: string) =>
    defects.push({ rule, subject, hurts })

  await signIn(page)

  /* ── R21: the stream stops halfway ───────────────────────────────────
     Served as a real event stream that simply ends after a few deltas, with
     no `error` and no `done` — which is exactly what a dropped connection
     looks like to the browser. */
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body:
        // Deliberately neutral wording: an earlier version of this fixture
        // said "연결이 끊겼습니다", and the rule below matched the fake answer
        // instead of the interface's own notice.
        `data: ${JSON.stringify({ type: 'delta', text: '여기까지 쓰다가' })}\n\n` +
        `data: ${JSON.stringify({ type: 'delta', text: ' 그 다음 문장이 오던 중' })}\n\n`,
    })
  })

  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('끊긴 스트림 확인')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
  await page.waitForTimeout(4000)

  const text = await page.evaluate(() => document.body.innerText)
  // Scoped to the answer itself. Read off the whole page, this matched a
  // conversation title in the sidebar and reported the turn as handled.
  const turn = await page.evaluate(() => {
    const last = [...document.querySelectorAll('[class*="animate-fade-up"]')].at(-1)
    return (last as HTMLElement | undefined)?.innerText ?? ''
  })

  checks++
  // What arrived is kept. Half an answer is worth more than none.
  if (!text.includes('여기까지 쓰다가')) {
    note('stream-partial', '끊긴 답변', '받아 둔 내용까지 사라진다')
  }

  checks++
  // And it must not look like it is still working.
  if (/생각하는 중/.test(text)) {
    note('stream-stuck', '끊긴 답변', '영원히 생각하는 중이라고 표시된다')
  }

  checks++
  // The composer has to come back. A send button stuck as "stop" means the
  // conversation is over as far as the user is concerned.
  const canSendAgain = await page.getByLabel('전송').count()
  if (canSendAgain === 0) {
    note('stream-recover', '입력창', '끊긴 뒤에 다시 보낼 수 없다')
  }

  checks++
  // Silence is the worst of the three: it reads as a finished answer that
  // simply stopped mid-sentence, and gets pasted into a report that way.
  if (!/끊|실패|오류|못했|멈췄/.test(turn)) {
    note('stream-silent', '끊긴 답변', '끊겼다는 사실을 말하지 않는다')
  }
  await page.unroute('**/api/sessions/*/messages')

  /* ── R22: what a cold visit costs ────────────────────────────────────
     Measured on a fresh context so nothing is cached, with the bytes counted
     off the wire rather than read off the build log. */
  const weighed = await page.context().newPage()
  let bytes = 0
  const perFile: Record<string, number> = {}
  weighed.on('response', async (res) => {
    const url = res.url()
    if (!/\.(js|css)(\?|$)/.test(url)) return
    const size = Number(res.headers()['content-length'] ?? 0)
    if (!size) return
    bytes += size
    perFile[url.split('/').pop() ?? url] = size
  })
  const started = Date.now()
  await weighed.goto('/', { waitUntil: 'load' })
  const loaded = Date.now() - started
  await weighed.close()

  checks++
  // 1 MB over the wire is roughly four seconds on the kind of link a lecture
  // hall hands out at capacity.
  if (bytes > 1_000_000) {
    note('cold-weight', `${Math.round(bytes / 1024)}kB`, '첫 화면을 받는 데 오래 걸린다')
  }
  checks++
  if (loaded > 5_000) {
    note('cold-time', `${loaded}ms`, '첫 화면이 늦게 뜬다')
  }

  const byRule = defects.reduce<Record<string, number>>((acc, d) => {
    acc[d.rule] = (acc[d.rule] ?? 0) + 1
    return acc
  }, {})
  await mkdir('audit', { recursive: true })
  await writeFile(
    'audit/stream-weight-audit.json',
    JSON.stringify(
      { checks, defects: defects.length, byRule, defectList: defects, bytes, loaded, perFile },
      null,
      2,
    ),
  )
  console.log(`checks=${checks} defects=${defects.length} bytes=${Math.round(bytes / 1024)}kB loaded=${loaded}ms`)
  console.log(JSON.stringify(byRule, null, 2))
  expect(checks).toBeGreaterThan(0)
})
