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

/** Narration is generated and billed (about 1,000 credits); video only shows its quote (a clip is 12,000). */
test('내레이션을 만들면 오디오 아티팩트로 남는다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)

  const before = await page.evaluate(
    async (fn) => (await eval(fn)('/api/me/usage?days=1'))?.cycle?.used ?? -1,
    AS_USER,
  )

  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '오디오' }).click()
  // No 효과음: nothing serves it.
  await page.getByRole('button', { name: /^유형/ }).click()
  await expect(page.getByRole('menuitem', { name: '효과음' })).toHaveCount(0)
  await expect(page.getByRole('menuitem', { name: '내레이션' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByText(/Enter 로 생성/)).toBeVisible()

  await page.getByLabel('프롬프트 입력').fill('다음 문장을 읽어줘: 자동 검증용 음성입니다.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // Only the clip this run created.
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
  // A duration proves the WAV container was built around the raw PCM.
  expect(stored.data.durationSec).toBeGreaterThan(0)

  // The ledger settles just after the artifact is written.
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
  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')
  await page.getByRole('button', { name: /^종류/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '영상' }).click()

  // The quote comes from the same table the pass-through bills from.
  await expect(page.getByText(/예상 [1-9][0-9,]* 크레딧/)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(/아직 준비 중/)).toHaveCount(0)
})
