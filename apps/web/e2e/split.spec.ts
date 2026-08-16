import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, {
    ...(init || {}),
    headers: { ...((init || {}).headers || {}), Authorization: 'Bearer ' + accessToken },
  })
  if (!r.ok || r.status === 204) return null
  return await r.json()
}`

/**
 * The reader works in a half-width browser window, so the chat column and the
 * artifact split at whatever ratio *they* need — and the ratio has to survive
 * the reload, or it is a setting they re-make every session.
 */
test('분할선은 끌어 옮긴 자리를 새로고침 뒤에도 지킨다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const session = await page.evaluate(async (fn) => {
    const rows = await eval(fn)('/api/sessions')
    const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
    return list.find((s: { artifactId: string | null }) => s.artifactId) ?? null
  }, AS_USER)
  test.skip(!session, '결과물이 붙은 대화가 아직 없습니다.')

  await page.goto(`/s/${session.id}`)
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel).toBeVisible({ timeout: 20_000 })
  const before = (await panel.boundingBox())!.width

  const handle = panel.getByRole('separator')
  await expect(handle).toBeVisible()
  const box = (await handle.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x - 260, box.y + box.height / 2, { steps: 12 })
  await page.mouse.up()
  const after = (await panel.boundingBox())!.width
  expect(after, `끌어도 안 넓어짐 ${before}→${after}`).toBeGreaterThan(before + 150)

  await page.reload()
  await expect(panel).toBeVisible({ timeout: 20_000 })
  const kept = (await panel.boundingBox())!.width
  expect(Math.abs(kept - after), `새로고침 뒤 ${after}→${kept}`).toBeLessThan(24)

  // Double-click hands the default back, so a drag is never a one-way door.
  await handle.dblclick()
  expect((await panel.boundingBox())!.width).toBeLessThan(after - 100)
})
