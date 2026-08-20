import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * 사용량 화면은 자기가 아는 숫자를 설명할 수 있어야 한다.
 *
 * Total and breakdown must come from the same source. The credit ledger is it:
 * pictures, clips and speech bill without producing a turn, so a screen that
 * totals the ledger and breaks down stored turns files every media credit
 * under 기타.
 *
 * Nothing here generates anything — the property under test is arithmetic.
 * These read what the seeded account has already spent and check the parts
 * add up to the whole.
 */

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

  // Three ways of dividing one number, so all three have to come to it.
  expect(models + usage.totals.otherCredits).toBe(usage.totals.credits)
  expect(surfaces).toBe(usage.totals.credits)
  expect(days).toBe(usage.totals.credits)

  // 기타 is the residue — a charge shared by several models — and never the
  // whole bill. Anything else means the breakdown has stopped explaining.
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

  // Scoped to the 화면별 card: the sidebar carries the same five words as
  // navigation links, and a bare text match finds those instead.
  const bySurface = page.locator('div').filter({ hasText: /^화면별/ }).last()
  const labels = { image: '이미지', av: '오디오/동영상' }
  for (const row of media) {
    await expect(bySurface.getByText(labels[row.kind as 'image' | 'av'], { exact: true })).toBeVisible()
    await expect(bySurface.getByText(row.credits.toLocaleString()).first()).toBeVisible()
  }

  // And the model that made them is named, rather than every model reading zero
  // while the total sits above them.
  const priced = usage.byModel.filter((row) => row.credits > 0)
  expect(priced.length).toBeGreaterThan(0)
})

test('과금이 없는 기간에도 일별 그래프는 무엇이 일어났는지 그린다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  const usage = await readUsage(page, '/api/me/usage')
  await expect(page.getByRole('heading', { name: '사용량' })).toBeVisible({ timeout: 20_000 })
  test.skip(usage.daily.length === 0, '이 계정은 이 기간에 아무것도 하지 않았습니다')

  // Self-hosted models bill nothing, so a credit axis for a busy month is a
  // flat line. The chart says which of the two it is plotting.
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
