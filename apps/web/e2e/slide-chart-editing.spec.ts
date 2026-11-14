import { expect, test } from '@playwright/test'
import { E2E_ADMIN, deckSelect, deckSelection, deckThumbs, saveDeck, signIn } from './helpers'

test('차트의 축·계열·수치를 고치면 차트 레이아웃과 데이터가 함께 저장된다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  const id = await page.evaluate(async (admin) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: admin.email, password: admin.password }),
    })
    const auth = await login.json()
    const headers = { 'content-type': 'application/json', Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const listed = await (await fetch('/api/artifacts?kind=deck', { headers })).json()
    const first = (Array.isArray(listed) ? listed : listed.items)[0]
    const full = await (await fetch(`/api/artifacts/${first.id}`, { headers })).json()
    const data = full.data ?? full
    data.slides[0] = {
      ...data.slides[0], layout: 'chart', title: '분기별 이용자', body: undefined,
      bullets: undefined, rows: undefined, metrics: undefined, bands: undefined,
      tiles: undefined, timeline: undefined,
      chart: { kind: 'bar', unit: '명', categories: ['1분기', '2분기'], series: [{ name: '가입자', values: [100, 140] }] },
    }
    await fetch(`/api/artifacts/${first.id}`, { method: 'PATCH', headers, body: JSON.stringify({ data }) })
    return first.id as string
  }, E2E_ADMIN)

  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  // The slide is editable in place; selecting the chart shows its data under the slide.
  await deckThumbs(page).first().click()
  await deckSelect(page, 'chart')
  const chart = deckSelection(page)
  await expect(chart.getByLabel('차트 종류')).toBeVisible()
  await chart.getByLabel('차트 종류').selectOption('line')
  await chart.getByLabel('차트 단위').fill('%')
  await chart.getByLabel('가로축 항목').fill('상반기, 하반기')
  await chart.getByLabel('1번째 계열 이름').fill('달성률')
  await chart.getByLabel('1번째 계열 값').fill('72, 91')
  await saveDeck(page)

  const stored = await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: admin.email, password: admin.password }),
    })
    const auth = await login.json()
    return (await (await fetch(`/api/artifacts/${artifactId}`, {
      headers: { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` },
    })).json()).data.slides[0]
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  expect(stored.layout).toBe('chart')
  expect(stored.chart).toEqual({ kind: 'line', unit: '%', categories: ['상반기', '하반기'], series: [{ name: '달성률', values: [72, 91] }] })

  // Grid editor rather than pipe syntax.
  await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json()
    const headers = { 'content-type': 'application/json', Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers })).json()
    const data = full.data ?? full
    data.slides[0] = { ...data.slides[0], layout: 'table', chart: undefined, rows: [['구분', '값'], ['기존', '10']] }
    await fetch(`/api/artifacts/${artifactId}`, { method: 'PATCH', headers, body: JSON.stringify({ data }) })
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await deckThumbs(page).first().click()
  await deckSelect(page, 'table')
  const grid = deckSelection(page)
  await grid.getByLabel('2행 2열').fill('25')
  await grid.getByRole('button', { name: '행 추가' }).click()
  await grid.getByLabel('3행 1열').fill('개선')
  await grid.getByLabel('3행 2열').fill('40')
  await grid.getByRole('button', { name: '3행 위로' }).click()
  await saveDeck(page)

  const table = await page.evaluate(async ([admin, artifactId]) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json()
    const full = await (await fetch(`/api/artifacts/${artifactId}`, { headers: { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` } })).json()
    return full.data.slides[0]
  }, [E2E_ADMIN, id] as [typeof E2E_ADMIN, string])
  expect(table.layout).toBe('table')
  expect(table.rows).toEqual([['구분', '값'], ['개선', '40'], ['기존', '25']])
})
