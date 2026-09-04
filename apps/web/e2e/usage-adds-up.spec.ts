import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** Usage totals and breakdowns come from the same ledger and add up. Nothing is generated. */

type Usage = {
  totals: { credits: number; requests: number; otherCredits: number }
  daily: { date: string; credits: number; requests: number }[]
  byModel: { model: string; credits: number; requests: number }[]
  bySurface: { kind: string; credits: number; requests: number }[]
}

async function readUsage(page: import('@playwright/test').Page, path: string) {
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes(path) && r.request().method() === 'GET'),
    page.goto(path === '/api/me/usage' ? '/usage' : '/admin/usage'),
  ])
  return (await response.json()) as Usage
}

test('내 사용량은 쓴 곳을 전부 말할 수 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const usage = await readUsage(page, '/api/me/usage')
  await expect(page.getByRole('heading', { name: '사용량' })).toBeVisible({ timeout: 20_000 })

  const models = usage.byModel.reduce((sum, row) => sum + row.credits, 0)
  const surfaces = usage.bySurface.reduce((sum, row) => sum + row.credits, 0)
  const days = usage.daily.reduce((sum, row) => sum + row.credits, 0)

  // Three ways of dividing one number.
  expect(models + usage.totals.otherCredits).toBe(usage.totals.credits)
  expect(surfaces).toBe(usage.totals.credits)
  expect(days).toBe(usage.totals.credits)

  // 기타 is a residue, never the whole bill.
  if (usage.totals.credits > 0) {
    expect(usage.totals.otherCredits).toBeLessThan(usage.totals.credits)
  }
})

test('그림과 영상에 쓴 크레딧이 화면에 그대로 보인다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const usage = await readUsage(page, '/api/me/usage')
  await expect(page.getByRole('heading', { name: '사용량' })).toBeVisible({ timeout: 20_000 })

  const media = usage.bySurface.filter((row) => row.kind === 'image' || row.kind === 'av')
  test.skip(media.length === 0, '이 계정은 이 기간에 그림이나 영상을 만들지 않았습니다')

  // Scoped to the 화면별 card: the sidebar carries the same words.
  const bySurface = page.locator('div').filter({ hasText: /^화면별/ }).last()
  const labels = { image: '이미지', av: '오디오/동영상' }
  for (const row of media) {
    await expect(bySurface.getByText(labels[row.kind as 'image' | 'av'], { exact: true })).toBeVisible()
    await expect(bySurface.getByText(row.credits.toLocaleString()).first()).toBeVisible()
  }

  // The model that made them is named.
  const priced = usage.byModel.filter((row) => row.credits > 0)
  expect(priced.length).toBeGreaterThan(0)
})

test('과금이 없는 기간에도 일별 그래프는 무엇이 일어났는지 그린다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const usage = await readUsage(page, '/api/me/usage')
  await expect(page.getByRole('heading', { name: '사용량' })).toBeVisible({ timeout: 20_000 })
  test.skip(usage.daily.length === 0, '이 계정은 이 기간에 아무것도 하지 않았습니다')

  // Self-hosted models bill nothing; the chart says which axis it plots.
  const heading = usage.totals.credits > 0 ? '일별 크레딧' : '일별 응답 수'
  await expect(page.getByText(heading)).toBeVisible()
  expect(usage.daily.some((day) => day.credits > 0 || day.requests > 0)).toBe(true)
})

test('관리자 집계도 같은 원장을 읽는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const usage = await readUsage(page, '/api/admin/usage')
  await expect(page.getByRole('heading', { name: '사용량' })).toBeVisible({ timeout: 20_000 })

  const models = usage.byModel.reduce((sum, row) => sum + row.credits, 0)
  const surfaces = usage.bySurface.reduce((sum, row) => sum + row.credits, 0)
  expect(models + usage.totals.otherCredits).toBe(usage.totals.credits)
  expect(surfaces).toBe(usage.totals.credits)
})
