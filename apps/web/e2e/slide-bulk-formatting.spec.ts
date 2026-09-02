import { expect, test } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

test('선택한 여러 장의 색과 글자 크기를 한 번에 바꾸고 덱 기본색을 보존한다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  const id = await page.evaluate(async (admin) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json(); const headers = { 'content-type': 'application/json', Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const listed = await (await fetch('/api/artifacts?kind=deck', { headers })).json(); const first = (Array.isArray(listed) ? listed : listed.items)[0]
    const full = await (await fetch(`/api/artifacts/${first.id}`, { headers })).json(); const data = full.data ?? full
    data.design = { accent: '#224466', ink: '#101820', muted: '#65727a', font: 'gothic', footer: '테스트 조직' }
    data.slides = [0, 1, 2].map((i) => ({ id: `bulk-${i}`, layout: 'bullets', title: `일괄 서식 ${i + 1}`, bullets: [`${i + 1}번째 내용`], ...(i === 2 ? { accent: '#999999', textScale: 0.8 } : {}) }))
    await fetch(`/api/artifacts/${first.id}`, { method: 'PATCH', headers, body: JSON.stringify({ data }) })
    return first.id as string
  }, E2E_ADMIN)

  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await page.getByRole('button', { name: '여러 장 선택' }).click()
  await page.getByLabel('1번 장 선택').check()
  await page.getByLabel('2번 장 선택').check()
  await expect(page.getByText('2장 선택')).toBeVisible()
  await page.getByLabel('일괄 강조색').fill('#c2410c')
  await page.getByRole('button', { name: '선택 장에 색 적용' }).click()
  await expect(page.getByRole('button', { name: '선택 장에 색 적용' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '선택 장에 색 적용' })).toBeEnabled({ timeout: 20_000 })
  await page.getByLabel('일괄 글자 크기').selectOption('1.2')
  await page.getByRole('button', { name: '크기 적용' }).click()
  await expect(page.getByRole('button', { name: '크기 적용' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '크기 적용' })).toBeEnabled({ timeout: 20_000 })
  await page.getByLabel('일괄 강조색').fill('#0f766e')
  await page.getByRole('button', { name: '덱 기본색으로 저장' }).click()
  await expect(page.getByRole('button', { name: '덱 기본색으로 저장' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '덱 기본색으로 저장' })).toBeEnabled({ timeout: 20_000 })
  await expect(page.getByText(/다른 곳에서 이미 수정/)).toHaveCount(0)
  await expect.poll(() => page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json(); const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers: { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` } })).json()
    return full.data.design?.accent
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string]), { timeout: 20_000 }).toBe('#0f766e')

  const stored = await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json(); const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers })).json()
    const files = await Promise.all(['pptx', 'pdf'].map(async (format) => { const response = await fetch(`/api/artifacts/${artifactId}/export?format=${format}`, { headers }); return { status: response.status, size: (await response.blob()).size } }))
    return { data: full.data, files }
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  expect(stored.data.design).toMatchObject({ accent: '#0f766e', ink: '#101820', muted: '#65727a', font: 'gothic', footer: '테스트 조직', visualStyle: 'editorial' })
  expect(stored.data.slides.slice(0, 2).every((slide: { accent: string; textScale: number }) => slide.accent === '#c2410c' && slide.textScale === 1.2)).toBeTruthy()
  expect(stored.data.slides[2].accent).toBe('#999999')
  expect(stored.data.slides[2].textScale).toBe(0.8)
  expect(stored.files.every((file: { status: number; size: number }) => file.status === 200 && file.size > 1_000)).toBeTruthy()
})
