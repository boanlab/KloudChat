import { expect, test } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

test('긴 표와 차트를 구조를 보존해 두 장으로 나눈다', async ({ page }) => {
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
    data.slides = data.slides.filter((slide: { title?: string }, i: number) => i === 0 || (!slide.title?.startsWith('부서별 실행 현황') && !slide.title?.startsWith('월별 성과 추이')))
    data.slides[0] = {
      ...data.slides[0], layout: 'table', title: '부서별 실행 현황', textScale: 1.1,
      bullets: undefined, body: undefined, chart: undefined, metrics: undefined,
      bands: undefined, tiles: undefined, timeline: undefined, image: undefined,
      rows: [['부서', '담당 과제'], ...Array.from({ length: 14 }, (_, i) => [`${i + 1}부서`, `${i + 1}번째 실행 과제와 현재 진행 상태`])],
    }
    await fetch(`/api/artifacts/${first.id}`, { method: 'PATCH', headers, body: JSON.stringify({ data }) })
    return first.id as string
  }, E2E_ADMIN)

  const open = async () => {
    await page.goto('/artifacts')
    await page.getByRole('tab', { name: /^슬라이드/ }).click()
    await page.locator('button.aspect-video').first().click()
    await page.getByRole('button', { name: '편집 도구' }).click()
  }
  await open()
  await page.getByRole('button', { name: '이 장을 두 장으로 나누기' }).click()
  await expect(page.getByText(/부서별 실행 현황 \(계속\)/).first()).toBeVisible({ timeout: 20_000 })

  let parts = await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json(); const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers })).json()
    return full.data.slides.filter((slide: { title: string }) => slide.title.startsWith('부서별 실행 현황'))
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  expect(parts).toHaveLength(2)
  expect(parts[0].rows[0]).toEqual(['부서', '담당 과제'])
  expect(parts[1].rows[0]).toEqual(['부서', '담당 과제'])
  expect(parts[0].rows.length + parts[1].rows.length).toBe(16)

  await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json(); const headers = { 'content-type': 'application/json', Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers })).json(); const data = full.data
    const categories = Array.from({ length: 14 }, (_, i) => `${i + 1}월`)
    data.slides[0] = { ...data.slides[0], layout: 'chart', title: '월별 성과 추이', rows: undefined, textScale: 1.2, chart: { kind: 'line', unit: '%', categories, series: [{ name: '달성률', values: categories.map((_, i) => 70 + i) }, { name: '목표', values: categories.map(() => 90) }] } }
    await fetch(`/api/artifacts/${artifactId}`, { method: 'PATCH', headers, body: JSON.stringify({ data }) })
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  await open()
  await page.getByRole('button', { name: '이 장을 두 장으로 나누기' }).click()
  await expect(page.getByText(/월별 성과 추이 \(계속\)/).first()).toBeVisible({ timeout: 20_000 })
  parts = await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json(); const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers })).json()
    return full.data.slides.filter((slide: { title: string }) => slide.title.startsWith('월별 성과 추이'))
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  expect(parts).toHaveLength(2)
  expect(parts.flatMap((part: { chart: { categories: string[] } }) => part.chart.categories)).toEqual(Array.from({ length: 14 }, (_, i) => `${i + 1}월`))
  for (const part of parts) expect(part.chart.series.every((series: { values: number[] }) => series.values.length === part.chart.categories.length)).toBeTruthy()
  const files = await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json(); const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    return Promise.all(['pptx', 'pdf'].map(async (format) => {
      const response = await fetch(`/api/artifacts/${artifactId}/export?format=${format}`, { headers })
      return { status: response.status, size: (await response.blob()).size }
    }))
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  expect(files.every((file) => file.status === 200 && file.size > 1_000)).toBeTruthy()
})
