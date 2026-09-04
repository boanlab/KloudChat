import { expect, test } from '@playwright/test'
import { openSidebar, signIn } from './helpers'

/** The sidebar asks before deleting a conversation, naming it; 취소 keeps it. */
test('사이드바에서 대화를 지우기 전에 물어본다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)

  const title = `삭제확인 ${Date.now().toString(36)}`
  await page.evaluate(async (name) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = await login.json()
    const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` }
    const r = await fetch('/api/sessions', { method: 'POST', headers: H, body: JSON.stringify({ kind: 'chat' }) })
    const { id } = await r.json()
    await fetch(`/api/sessions/${id}`, { method: 'PATCH', headers: H, body: JSON.stringify({ title: name }) })
  }, title)

  await page.goto('/')
  await openSidebar(page)
  const row = page.locator('aside').locator('div.group').filter({ hasText: title }).first()
  await expect(row).toBeVisible({ timeout: 15_000 })

  // 취소 keeps it.
  await row.getByRole('button', { name: '메뉴' }).click()
  await page.getByRole('menuitem', { name: '삭제' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: `${title} 삭제` })).toBeVisible()
  await dialog.getByRole('button', { name: '취소' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(row).toBeVisible()

  // 삭제 sends the request only now.
  await row.getByRole('button', { name: '메뉴' }).click()
  await page.getByRole('menuitem', { name: '삭제' }).click()
  await Promise.all([
    page.waitForResponse(
      (r) => /\/api\/sessions\/[0-9a-f]{32}$/.test(r.url()) && r.request().method() === 'DELETE',
    ),
    page.getByRole('dialog').getByRole('button', { name: '삭제', exact: true }).click(),
  ])
  await expect(page.locator('aside').getByText(title)).toHaveCount(0, { timeout: 15_000 })
})
