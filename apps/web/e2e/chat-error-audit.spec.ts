import { expect, test, type Page } from '@playwright/test'

/** A send the client itself refuses or loses — a 429, a dropped connection — as the person
 *  sees it: said in words beside the composer, with nothing left spinning. Failures inside
 *  the stream are stored by the server and shown from its rows, which this faked stream
 *  cannot provide, so they are not here. Screenshots go to the test's output folder. */

const cases: { name: string; kind: 'chat' | 'report'; reply: (route: import('@playwright/test').Route) => Promise<void> }[] = [
  { name: 'rate-429', kind: 'chat', reply: (r) => r.fulfill({ status: 429, json: { detail: 'rate_limited' } }) },
  { name: 'network-abort', kind: 'chat', reply: (r) => r.abort('connectionreset') },
]

async function send(page: Page, kind: 'chat' | 'report', text: string) {
  await page.goto(`/new/${kind}`)
  const input = page.getByLabel('프롬프트 입력')
  await input.fill(text)
  await input.press('Enter')
}

for (const c of cases) {
  test(`error shown: ${c.name}`, async ({ page }, testInfo) => {
    test.setTimeout(120_000)
    const problems: string[] = []
    page.on('pageerror', (e) => problems.push(e.message))
    // The running app's own account, given by the caller; no sign-up, no form retries.
    const login = await page.request.post('/api/auth/login', {
      data: { email: process.env.KC_EMAIL, password: process.env.KC_PASSWORD },
    })
    expect(login.ok()).toBeTruthy()
    await page.route('**/api/sessions/*/messages', async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      await c.reply(route)
    })
    await send(page, c.kind, `오류 표시 점검 ${c.name}`)
    // The failure is said in words somewhere on the screen.
    await expect(page.getByText(/못했습니다|응답하지 않습니다|거부되었습니다|연결|시간이 초과|다시 시도/).first()).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(1500)
    await page.screenshot({ path: testInfo.outputPath(`${c.name}.png`) })
    // Nothing keeps spinning once the turn has failed, and the composer takes the next one.
    await expect(page.getByLabel('중지')).toBeHidden({ timeout: 10_000 })
    await expect(page.getByLabel('프롬프트 입력')).toBeEditable()
    expect(problems).toEqual([])
  })
}
