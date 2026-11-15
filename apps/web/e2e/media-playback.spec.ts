import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** Generated audio and video play inside the app. Uses existing clips; skips when there are none. */

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

/** Generated audio and video live in 파일함 › 만든 파일 now (not on the 아티팩트 page): the file
 *  is found through its artifact's `src`, then opened with the `open` param. */
async function openNewest(page: import('@playwright/test').Page, kind: string) {
  const fileId = await page.evaluate(
    async ([fn, k]) => {
      const rows = await eval(fn as string)(`/api/artifacts?kind=${k}&limit=1`)
      const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
      if (!list.length) return ''
      const full = await eval(fn as string)(`/api/artifacts/${list[0].id}`)
      const src = String((full?.data ?? full)?.src ?? '')
      return /\/files\/([0-9a-f]{32})\/content/.exec(src)?.[1] ?? ''
    },
    [AS_USER, kind] as const,
  )
  test.skip(!fileId, `${kind} 아티팩트가 없습니다`)
  await page.goto(`/files?tab=made&open=${fileId}`)
}

test('만든 오디오를 앱 안에서 재생할 수 있다', async ({ page }) => {
  await signIn(page)
  await openNewest(page, 'audio')

  const player = page.getByTestId('media-element')
  await expect(player).toBeVisible({ timeout: 20_000 })
  await expect(player).toHaveAttribute('src', /\/files\/[0-9a-f]+/)

  // A duration means the container decoded; raw PCM without a WAV header gives NaN.
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
  await openNewest(page, 'video')

  const player = page.getByTestId('media-element')
  await expect(player).toBeVisible({ timeout: 20_000 })
  await expect(player).toHaveAttribute('src', /\/files\/[0-9a-f]+/)

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
