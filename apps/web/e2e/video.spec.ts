import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Video generation and playback on the audio/video surface.
 *
 * Expensive: a 4-second 720p silent clip on the cheapest model is 12,000
 * credits. So one clip is generated and generation, pricing and playback are
 * all asserted against it.
 */

const AS_USER = `async (path) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + accessToken } })
  return r.ok ? await r.json() : null
}`

test('영상을 만들면 견적대로 걷히고 앱 안에서 재생된다', async ({ page }) => {
  // The upstream takes minutes and the worker polls every six seconds.
  test.setTimeout(900_000)
  await signIn(page)

  const seen = await page.evaluate(async (fn) => {
    const rows = await eval(fn)('/api/artifacts')
    const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
    return list.filter((a: { kind: string }) => a.kind === 'video').map((a: { id: string }) => a.id)
  }, AS_USER)
  const before = await page.evaluate(
    async (fn) => (await eval(fn)('/api/me/usage?days=1'))?.cycle?.used ?? -1,
    AS_USER,
  )

  await page.goto('/new/av')
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitem', { name: '영상' }).click()

  // Switching to video has to bring a video model with it. The cheapest model on
  // this surface is a speech model, and it stayed selected — so the composer
  // sat there refusing every clip with a message about the combination.
  await page.getByRole('button', { name: /^해상도/ }).click()
  await page.getByRole('menuitem', { name: '720p' }).click()
  await page.keyboard.press('Escape')

  // 4 seconds, 720p, silent, on the cheapest model: 3,000 credits a second.
  const quoted = await page
    .getByText(/예상 [\d,]+ 크레딧/)
    .first()
    .innerText()
  const quote = Number(quoted.replace(/[^\d]/g, ''))
  expect(quote, '견적이 표시되지 않았습니다').toBeGreaterThan(0)

  await page.getByLabel('프롬프트 입력').fill('책상 위 커피잔에서 김이 천천히 올라온다')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // The card appears immediately and carries the job while the clip is made —
  // a request that is being paid for is never invisible.
  await expect(page.getByText(/만드는 중|대기/).first()).toBeVisible({ timeout: 60_000 })

  const fresh = async () =>
    await page.evaluate(
      async ([fn, ids]) => {
        const rows = await eval(fn as string)('/api/artifacts')
        const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
        return (
          list.find(
            (a: { kind: string; id: string }) =>
              a.kind === 'video' && !(ids as string[]).includes(a.id),
          ) ?? null
        )
      },
      [AS_USER, seen] as const,
    )

  await expect
    .poll(fresh, { timeout: 840_000, intervals: [10_000], message: '영상이 오지 않았습니다' })
    .not.toBeNull()
  const stored = await fresh()
  expect(stored.data.src).toContain('/api/files/')
  expect(stored.data.durationSec).toBeGreaterThan(0)

  // Billed what was quoted. `duration_seconds` is silently ignored upstream —
  // the accepted field is `duration` — and a request that used the wrong name
  // produced an eight-second clip nobody asked for at twice the price.
  const after = await page.evaluate(
    async (fn) => (await eval(fn)('/api/me/usage?days=1')).cycle.used,
    AS_USER,
  )
  const charged = after - before
  expect(charged, '영상 생성이 과금되지 않았습니다').toBeGreaterThan(0)
  // Within a tenth: the upstream's own figure is authoritative and can differ
  // slightly from the pass-through's fixed price, but not by a multiple.
  expect(Math.abs(charged - quote) / quote).toBeLessThan(0.1)

  // And it plays, in the app, from the job card — not only by opening the file.
  const player = page.locator('video[controls]').first()
  await expect(player).toBeVisible({ timeout: 30_000 })
  await expect(player).toHaveAttribute('src', /\/api\/files\/[0-9a-f]+\/content/)
  await expect
    .poll(
      async () =>
        await player.evaluate((el: HTMLVideoElement) =>
          Number.isFinite(el.duration) ? el.duration : 0,
        ),
      { timeout: 60_000, message: '영상이 디코딩되지 않았습니다' },
    )
    .toBeGreaterThan(0)
})
