import { mkdir, writeFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** A dropped stream is kept, said and recoverable; a cold visit's weight and time are measured.
 *  Writes `audit/stream-weight-audit.json`; never fails. */

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

  // R21: an event stream that ends after a few deltas, with no `error` and no `done`.
  await page.route('**/api/sessions/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body:
        // Neutral wording, so the notice rule below cannot match the fake answer.
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
  // The answer itself; the sidebar carries titles that could match.
  const turn = await page.evaluate(() => {
    const last = [...document.querySelectorAll('[class*="animate-fade-up"]')].at(-1)
    return (last as HTMLElement | undefined)?.innerText ?? ''
  })

  checks++
  // What arrived is kept.
  if (!text.includes('여기까지 쓰다가')) {
    note('stream-partial', '끊긴 답변', '받아 둔 내용까지 사라진다')
  }

  checks++
  if (/생각하는 중/.test(text)) {
    note('stream-stuck', '끊긴 답변', '영원히 생각하는 중이라고 표시된다')
  }

  checks++
  // The composer comes back.
  const canSendAgain = await page.getByLabel('전송').count()
  if (canSendAgain === 0) {
    note('stream-recover', '입력창', '끊긴 뒤에 다시 보낼 수 없다')
  }

  checks++
  // The cut is said.
  if (!/끊|실패|오류|못했|멈췄/.test(turn)) {
    note('stream-silent', '끊긴 답변', '끊겼다는 사실을 말하지 않는다')
  }
  await page.unroute('**/api/sessions/*/messages')

  // R22: a cold visit's cost, counted off the wire.
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
  // 1 MB is roughly four seconds on a crowded lecture-hall link.
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
