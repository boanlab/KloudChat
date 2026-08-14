import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Media that was paid for has to play inside the app.
 *
 * Spends no credits: it asserts against clips the audio and video specs have
 * already generated, and skips when there are none.
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

async function openNewest(page: import('@playwright/test').Page, tab: string, kind: string) {
  const exists = await page.evaluate(
    async ([fn, k]) => {
      const rows = await eval(fn as string)('/api/artifacts')
      const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
      return list.some((a: { kind: string }) => a.kind === k)
    },
    [AS_USER, kind] as const,
  )
  test.skip(!exists, `${kind} 아티팩트가 없습니다`)
  await page.getByRole('tab', { name: new RegExp(`^${tab}`) }).click()
  await page.locator('button.aspect-video').first().click()
}

test('만든 오디오를 앱 안에서 재생할 수 있다', async ({ page }) => {
  await signIn(page)
  await page.goto('/artifacts')
  await openNewest(page, '오디오', 'audio')

  // A real element with real controls, pointed at the stored file.
  const player = page.locator('audio[controls]')
  await expect(player).toBeVisible({ timeout: 20_000 })
  await expect(player).toHaveAttribute('src', /\/api\/files\/[0-9a-f]+\/content/)

  // And it decodes: the browser reports a duration only once it has read the
  // container. Raw PCM without the WAV header would give NaN here.
  await expect
    .poll(
      async () =>
        await player.evaluate((el: HTMLAudioElement) =>
          Number.isFinite(el.duration) ? el.duration : 0,
        ),
      { timeout: 20_000, message: '오디오가 디코딩되지 않았습니다' },
    )
    .toBeGreaterThan(0)
})

test('만든 영상을 앱 안에서 재생할 수 있다', async ({ page }) => {
  await signIn(page)
  await page.goto('/artifacts')
  await openNewest(page, '동영상', 'video')

  const player = page.locator('video[controls]')
  await expect(player).toBeVisible({ timeout: 20_000 })
  await expect(player).toHaveAttribute('src', /\/api\/files\/[0-9a-f]+\/content/)

  await expect
    .poll(
      async () =>
        await player.evaluate((el: HTMLVideoElement) =>
          Number.isFinite(el.duration) ? el.duration : 0,
        ),
      { timeout: 30_000, message: '영상이 디코딩되지 않았습니다' },
    )
    .toBeGreaterThan(0)
})
