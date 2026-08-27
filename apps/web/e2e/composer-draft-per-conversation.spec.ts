import { expect, test } from '@playwright/test'
import { openSidebar, signIn } from './helpers'

/**
 * A draft stays with the place it was typed. It used to be lost on a home-tab
 * change (the composer remounts there) and carried into the next conversation
 * (the composer stays mounted there) — two opposite failures of the same
 * missing rule.
 */
test('홈에서 탭을 오가도 각 탭의 초안은 그대로다', async ({ page }) => {
  await signIn(page)
  await page.goto('/')

  const composer = page.getByLabel('프롬프트 입력')
  await page.getByRole('button', { name: '보고서', exact: true }).first().click()
  await composer.fill('3월 정기 점검 결과를 정리해 줘.')

  await page.getByRole('button', { name: '슬라이드', exact: true }).first().click()
  await expect(composer).toHaveValue('')
  await composer.fill('발표용 5장')

  await page.getByRole('button', { name: '보고서', exact: true }).first().click()
  await expect(composer).toHaveValue('3월 정기 점검 결과를 정리해 줘.')
  await page.getByRole('button', { name: '슬라이드', exact: true }).first().click()
  await expect(composer).toHaveValue('발표용 5장')
})

test('대화를 오가도 초안은 자기 대화에만 남는다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)

  const stamp = Date.now().toString(36)
  const titles = [`초안 A ${stamp}`, `초안 B ${stamp}`]
  const [idA] = await page.evaluate(async (names) => {
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
    const ids: string[] = []
    for (const title of names) {
      const r = await fetch('/api/sessions', { method: 'POST', headers: H, body: JSON.stringify({ kind: 'chat' }) })
      const { id } = await r.json()
      ids.push(id)
      await fetch(`/api/sessions/${id}`, { method: 'PATCH', headers: H, body: JSON.stringify({ title }) })
    }
    return ids
  }, titles)

  // Opened by URL, not by the sidebar from 홈: the session screen is a lazy
  // route, and for the first ~100ms after the URL changes the home screen —
  // and its composer — is still what is mounted. Typing into that box put the
  // draft under the home key, and the test read a bug the app does not have.
  await page.goto(`/s/${idA}`)
  const composer = page.getByLabel('프롬프트 입력')
  await expect(composer).toBeVisible()
  await composer.fill('A에서 쓰던 문장')

  // A route change, not a reload: the composer stays mounted, which is exactly
  // where the draft used to follow the person.
  await openSidebar(page)
  await page.locator('aside').getByText(titles[1]).first().click()
  await expect(composer).toHaveValue('')

  await openSidebar(page)
  await page.locator('aside').getByText(titles[0]).first().click()
  await expect(composer).toHaveValue('A에서 쓰던 문장')
})
