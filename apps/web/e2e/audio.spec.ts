import { expect, test } from '@playwright/test'
import { signIn, surfaceOn } from './helpers'

/** The API holds its token in memory, so a cookie fetch is anonymous. */
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

/**
 * The audio/video surface produces narration and video.
 *
 * One audio clip per run, at roughly 1,000 credits. No video is generated
 * here — a 4-second clip is 12,000 credits — so instead this checks that the
 * quote is shown before any of it is spent.
 */
test('내레이션을 만들면 오디오 아티팩트로 남는다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)

  const before = await page.evaluate(
    async (fn) => (await eval(fn)('/api/me/usage?days=1'))?.cycle?.used ?? -1,
    AS_USER,
  )

  // Skipped where the workspace has this surface off. `image` and `av` spend
  // credits per generation and default to off, and the screen for a surface
  // that is off carries no composer to drive.
  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')
  // The kind control is a dropdown labelled with its current value.
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '오디오' }).click()
    // Sound effects are absent from the list: nothing serves them.
  await page.getByRole('button', { name: /^유형/ }).click()
  await expect(page.getByRole('menuitem', { name: '효과음' })).toHaveCount(0)
  await expect(page.getByRole('menuitem', { name: '내레이션' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByText(/Enter 로 생성/)).toBeVisible()

  await page.getByLabel('프롬프트 입력').fill('다음 문장을 읽어줘: 자동 검증용 음성입니다.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

    // Only the clip this run created. Matching "any audio artifact" latches
    // onto one left by an earlier run, and then the billing being asserted is
    // billing this run never incurred.
  const before_ids = await page.evaluate(async (fn) => {
    const rows = await eval(fn)('/api/artifacts')
    const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
    return list.filter((a: { kind: string }) => a.kind === 'audio').map((a: { id: string }) => a.id)
  }, AS_USER)

  const fresh = async () =>
    await page.evaluate(
      async ([fn, seen]) => {
        const rows = await eval(fn as string)('/api/artifacts')
        const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
        return (
          list.find(
            (a: { kind: string; id: string }) =>
              a.kind === 'audio' && !(seen as string[]).includes(a.id),
          ) ?? null
        )
      },
      [AS_USER, before_ids] as const,
    )

  await expect
    .poll(fresh, { timeout: 240_000, message: '오디오 아티팩트가 생기지 않았습니다' })
    .not.toBeNull()
  const stored = await fresh()

  expect(stored.data.audioKind).toBe('narration')
  expect(stored.data.src).toContain('/api/files/')
  // Raw PCM wrapped in a WAV header here — without it the browser has bytes it
  // cannot play, so a duration proves the container was built.
  expect(stored.data.durationSec).toBeGreaterThan(0)

  // Settled just after the artifact is written, so this polls rather than reads
  // once. The ledger is the source of truth — `messages.usage` is not.
  await expect
    .poll(
      async () =>
        await page.evaluate(
          async (fn) => (await eval(fn)('/api/me/usage?days=1')).cycle.used,
          AS_USER,
        ),
      { timeout: 30_000, message: '오디오 생성이 과금되지 않았습니다' },
    )
    .toBeGreaterThan(before)
})

test('영상 모드는 만들기 전에 값을 알려 준다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  // Skipped where the workspace has this surface off. `image` and `av` spend
  // credits per generation and default to off, and the screen for a surface
  // that is off carries no composer to drive.
  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '영상' }).click()

  // The quote comes from the same table the pass-through bills from. A clip
  // once came back at twice the quoted price because the request named a field
  // the API ignores, so the number being shown at all is the point.
  await expect(page.getByText(/예상 [1-9][0-9,]* 크레딧/)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(/아직 준비 중/)).toHaveCount(0)
})
