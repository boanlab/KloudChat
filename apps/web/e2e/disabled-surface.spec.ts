import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A surface an administrator has switched off.
 *
 * The sidebar and home screen hide these already, but the URL keeps working —
 * a bookmark, a shared link, an address-bar completion — and the composer that
 * came up looked entirely normal. Typing into it did nothing: the server
 * refuses to open the session and the refusal escaped as an unhandled promise,
 * so the screen said nothing at all.
 *
 * Skipped when every surface is on, because then there is nothing to assert.
 */
test('꺼져 있는 화면은 그렇다고 말한다', async ({ page }) => {
  await signIn(page)
  const enabled: string[] = await page.evaluate(async () => {
    // `/api/auth/config` is where a signed-in client reads this; there is no
    // `/api/settings`, and fetching it 404s into an empty list — which made
    // the skip below never fire and the assertion run against a live surface.
    const r = await fetch('/api/auth/config')
    return r.ok ? ((await r.json()).enabledKinds ?? []) : []
  })
  const off = ['image', 'av'].find((k) => !enabled.includes(k))
  test.skip(!off, '이 인스턴스는 모든 화면이 켜져 있습니다.')

  await page.goto(`/new/${off}`)
  await expect(page.getByText(/기능이 꺼져 있습니다/)).toBeVisible({ timeout: 20_000 })
  // And an exit, rather than a dead end.
  await expect(page.getByRole('button', { name: '홈으로' })).toBeVisible()
  // The composer must not be there offering something that cannot happen.
  await expect(page.getByLabel('프롬프트 입력')).toHaveCount(0)
})

/**
 * The same rule, one screen earlier.
 *
 * Turning a surface off is two things — it leaves the UI *and* the server
 * refuses to open a session of that kind. The sign-in screen was outside both:
 * it listed all five from a constant while the account it was about to sign
 * somebody into had three. Image and audio/video default to off, so this was
 * the first screen of every stock install promising two features that were not
 * there.
 *
 * Counted rather than seen: the brand column is `hidden lg:flex`, so at tablet
 * width the rows are in the document and invisible. What must change is that
 * they are not written at all.
 */
test('꺼져 있는 화면은 로그인 화면에서도 약속되지 않는다', async ({ page }) => {
  await page.goto('/')
  // The public configuration, read the way the sign-in screen reads it — no
  // session required, which is the whole point of that endpoint.
  const enabled: string[] = await page.evaluate(async () => {
    const r = await fetch('/api/auth/config')
    return r.ok ? ((await r.json()).enabledKinds ?? []) : []
  })
  const LABEL: Record<string, string> = { image: '이미지', av: '오디오/동영상' }
  const off = Object.keys(LABEL).filter((k) => !enabled.includes(k))
  test.skip(off.length === 0, '이 인스턴스는 모든 화면이 켜져 있습니다.')

  for (const kind of off) {
    await expect(
      page.getByText(LABEL[kind], { exact: true }),
      `${LABEL[kind]} 은 꺼져 있는데 로그인 화면이 약속합니다`,
    ).toHaveCount(0)
  }
  // Chat cannot be switched off, so its row is what proves the list still
  // renders rather than having been emptied by the filter.
  await expect(page.getByText('챗', { exact: true })).toHaveCount(1)
})
