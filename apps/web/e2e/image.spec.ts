import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The image surface produces a picture.
 *
 * This costs real money — roughly 4,400 credits per image on the default
 * model — so exactly one is generated.
 */

/** The API uses a bearer token held in memory, so a cookie fetch is anonymous. */
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

test('이미지를 만들면 아티팩트로 남고 크레딧이 걷힌다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)

  const spentBefore = await page.evaluate(
    async (fn) => (await eval(fn)('/api/me/usage?days=1'))?.cycle?.used ?? -1,
    AS_USER,
  )

  await page.goto('/new/image')
  // The picker offers image models, and quotes the price of a picture rather
  // than of a thousand tokens.
  await expect(page.getByText(/예상 [1-9][0-9,]* 크레딧/)).toBeVisible({ timeout: 20_000 })

  await page.getByLabel('프롬프트 입력').fill('흰 배경에 놓인 파란 자물쇠, 아주 단순한 평면 일러스트')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  // The picture itself, in the conversation, served inline from the file store.
  const image = page.locator('img[src*="/api/files/"]').first()
  await expect(image).toBeVisible({ timeout: 240_000 })
    // Visible and loaded are different things: an `<img>` has layout size even
    // behind a 401. `naturalWidth` stays 0 until the image actually decodes.
  await expect
    .poll(async () => await image.evaluate((el: HTMLImageElement) => el.naturalWidth), {
      timeout: 30_000,
      message: '이미지가 로드되지 않았습니다',
    })
    .toBeGreaterThan(0)

  const stored = await page.evaluate(async (fn) => {
    const rows = await eval(fn)('/api/artifacts')
    const list = Array.isArray(rows) ? rows : rows.items
    return list.find((a: { kind: string }) => a.kind === 'image') ?? null
  }, AS_USER)
  expect(stored, '이미지 아티팩트가 없습니다').not.toBeNull()
  expect(stored.data.src).toContain('/api/files/')

  // Billed. The turn records what it was charged, but this reads the ledger:
  // the figure on the message is a copy for the reader, and the account is the
  // account.
  const spentAfter = await page.evaluate(
    async (fn) => (await eval(fn)('/api/me/usage?days=1')).cycle.used,
    AS_USER,
  )
  expect(spentAfter, '이미지 생성이 과금되지 않았습니다').toBeGreaterThan(spentBefore)
})
