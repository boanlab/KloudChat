import { expect, test } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

test('잘린 슬라이드를 감지하고 가독성 한계까지 자동 맞춘 뒤 파일로 내보낸다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  const id = await page.evaluate(async (admin) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json()
    const headers = { 'content-type': 'application/json', Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const listed = await (await fetch('/api/artifacts?kind=deck', { headers })).json()
    const first = (Array.isArray(listed) ? listed : listed.items)[0]
    const full = await (await fetch(`/api/artifacts/${first.id}`, { headers })).json()
    const data = full.data ?? full
    data.slides = data.slides.filter((slide: { title?: string }, i: number) => i === 0 || !slide.title?.startsWith('한 장에 지나치게 많은 내용이 들어간 경우'))
    data.slides[0] = {
      ...data.slides[0], layout: 'bullets', title: '한 장에 지나치게 많은 내용이 들어간 경우',
      textScale: 1.2, body: undefined, rows: undefined, chart: undefined, metrics: undefined,
      bands: undefined, tiles: undefined, timeline: undefined, image: undefined,
      bullets: Array.from({ length: 24 }, (_, i) => `${i + 1}번째 검토 항목은 화면과 내보낸 파일에서 잘리지 않아야 합니다.`),
    }
    await fetch(`/api/artifacts/${first.id}`, { method: 'PATCH', headers, body: JSON.stringify({ data }) })
    return first.id as string
  }, E2E_ADMIN)

  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await page.getByRole('tab', { name: '검토', exact: true }).click()
  await expect(page.getByRole('button', { name: '잘림 위험 장으로 이동' })).toBeVisible()
  await page.getByRole('tab', { name: '홈', exact: true }).click()
  await page.getByRole('tab', { name: '편집', exact: true }).click()
  await expect(page.getByRole('button', { name: '잘린 내용 자동 맞춤' })).toBeVisible()
  await page.getByRole('button', { name: '잘린 내용 자동 맞춤' }).click()
  await expect(page.getByText('한 장에 넣기 어렵습니다.')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: '저장', exact: true }).click()

  const result = await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json()
    const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers })).json()
    const files = await Promise.all(['pptx', 'pdf'].map(async (format) => {
      const response = await fetch(`/api/artifacts/${artifactId}/export?format=${format}`, { headers })
      return { format, status: response.status, size: (await response.blob()).size }
    }))
    return { scale: full.data.slides[0].textScale, files }
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  expect(result.scale).toBe(0.65)
  expect(result.files).toEqual([
    expect.objectContaining({ format: 'pptx', status: 200 }),
    expect.objectContaining({ format: 'pdf', status: 200 }),
  ])
  expect(result.files.every((file) => file.size > 1_000)).toBeTruthy()

  await page.getByRole('tab', { name: '편집', exact: true }).click()
  await expect(page.getByRole('button', { name: '잘린 내용 자동 맞춤' })).toBeVisible()
  await page.getByRole('button', { name: '잘린 내용 자동 맞춤' }).click()
  await expect(page.getByRole('button', { name: '내용을 다음 장으로 나누기' })).toBeVisible()
  await page.getByRole('button', { name: '내용을 다음 장으로 나누기' }).click()
  await expect(page.getByText(/\(계속\)/).first()).toBeVisible({ timeout: 20_000 })

  const split = await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json()
    const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers })).json()
    const parts = full.data.slides.filter((slide: { title: string }) => slide.title.startsWith('한 장에 지나치게 많은 내용이 들어간 경우'))
    const exports = await Promise.all(['pptx', 'pdf'].map(async (format) => {
      const response = await fetch(`/api/artifacts/${artifactId}/export?format=${format}`, { headers })
      return { status: response.status, size: (await response.blob()).size }
    }))
    return { parts, exports }
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  expect(split.parts).toHaveLength(2)
  expect(split.parts.map((part: { bullets: string[] }) => part.bullets.length)).toEqual([12, 12])
  expect(split.parts.every((part: { textScale?: number }) => part.textScale === undefined)).toBeTruthy()
  expect(split.exports.every((file: { status: number; size: number }) => file.status === 200 && file.size > 1_000)).toBeTruthy()
})
