import { expect, test } from '@playwright/test'
import { openSidebar, signIn } from './helpers'

/** Deleting the open conversation leaves its page; its address afterwards says it is gone, with no retry. */
test('열어 둔 대화를 지우면 그 화면을 떠나고, 지운 주소는 없다고 말한다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)

  const title = `열린삭제 ${Date.now().toString(36)}`
  const id = await page.evaluate(async (name) => {
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
    return id as string
  }, title)

  await page.goto(`/s/${id}`)
  await openSidebar(page)
  const row = page.locator('aside').locator('div.group').filter({ hasText: title }).first()
  await expect(row).toBeVisible({ timeout: 15_000 })

  await row.getByRole('button', { name: '메뉴' }).click()
  await page.getByRole('menuitem', { name: '삭제' }).click()
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().endsWith(`/api/sessions/${id}`) && r.request().method() === 'DELETE',
    ),
    page.getByRole('dialog').getByRole('button', { name: '삭제', exact: true }).click(),
  ])
  // The page left the deleted conversation and the list does not bring it back.
  await expect(page).not.toHaveURL(new RegExp(`/s/${id}`), { timeout: 15_000 })
  await expect(page.locator('aside').getByText(title)).toHaveCount(0)

  // Its address now says it is gone instead of a load failure.
  await page.goto(`/s/${id}`)
  await expect(page.getByText('대화를 찾을 수 없습니다.', { exact: false })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('최신 내용을 불러오지 못했습니다.')).toHaveCount(0)
  await page.getByRole('button', { name: '새 대화' }).last().click()
  await expect(page).not.toHaveURL(new RegExp(`/s/${id}`))
})
