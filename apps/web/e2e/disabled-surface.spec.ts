import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** A switched-off surface says so at its URL and on the sign-in screen. Skipped when every surface is on. */
test('꺼져 있는 화면은 그렇다고 말한다', async ({ page }) => {
  await signIn(page)
  const enabled: string[] = await page.evaluate(async () => {
    const r = await fetch('/api/auth/config')
    return r.ok ? ((await r.json()).enabledKinds ?? []) : []
  })
  const off = ['image', 'av'].find((k) => !enabled.includes(k))
  test.skip(!off, '이 인스턴스는 모든 화면이 켜져 있습니다.')

  await page.goto(`/new/${off}`)
  await expect(page.getByText(/기능이 꺼져 있습니다/)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('button', { name: '홈으로' })).toBeVisible()
  await expect(page.getByLabel('프롬프트 입력')).toHaveCount(0)
})

/** Counted, not seen: the brand column is `hidden lg:flex` at tablet width. */
test('꺼져 있는 화면은 로그인 화면에서도 약속되지 않는다', async ({ page }) => {
  await page.goto('/')
  // Public configuration; no session required.
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
  // Chat cannot be switched off, so its row proves the list still renders.
  await expect(page.getByText('챗', { exact: true })).toHaveCount(1)
})
