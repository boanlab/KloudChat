import { expect, test, type Page } from '@playwright/test'
import { hubFixture } from './customize-fixture'
import { openTool, toolNames } from './composer-tools'

/**
 * 양식 is a document's structure (the shipped report and slide templates), 디자인 is its look.
 * The composer picks a 양식; the hub keeps the 양식 catalogue on its own tab, and its 디자인 tab
 * holds designs only. Fixture data only; NAMING_SHOTS (a directory) also saves screenshots.
 */

test.use({ serviceWorkers: 'block' })

const SHOTS = process.env.NAMING_SHOTS ?? ''

async function shot(page: Page, name: string, phone: boolean) {
  if (!SHOTS) return
  if (!phone) await page.setViewportSize({ width: 1280, height: 800 })
  await page.waitForLoadState('networkidle').catch(() => undefined)
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/naming-${name}-${phone ? 'phone' : 'desktop'}.png` })
}

test('the composer picks a 양식, and the gallery says so', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const phone = testInfo.project.name === 'phone'
  await page.goto('/new/report')
  // ▦ lives in 「+」 (a bottom sheet on a phone).
  await expect(page.getByRole('button', { name: '도구', exact: true })).toBeVisible({ timeout: 15_000 })
  const names = await toolNames(page)
  expect(names).toContain('양식 고르기')
  expect(names).not.toContain('서식 고르기')
  await openTool(page, '양식 고르기')

  const dialog = page.getByRole('dialog', { name: '양식 고르기' })
  await expect(dialog).toBeVisible({ timeout: 15_000 })
  await expect(dialog.getByRole('heading', { name: '양식 고르기' })).toBeVisible()
  await expect(dialog.getByLabel('양식 검색')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '이 양식으로 시작' }).first()).toBeVisible()
  await expect(dialog).not.toContainText('서식')
  await shot(page, 'gallery', phone)

  // 「모두 보기」 goes to the hub's 양식 tab.
  await dialog.getByRole('link', { name: '모두 보기' }).click()
  await expect(page).toHaveURL(/\/customize\?tab=templates$/)
  await expect(page.getByRole('tab', { name: '양식' })).toHaveAttribute('aria-selected', 'true')
  expect(state.problems).toEqual([])
})

test('the hub has a 양식 tab with the templates; 디자인 holds designs only', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const phone = testInfo.project.name === 'phone'

  await page.goto('/customize?tab=templates')
  await expect(page.getByRole('heading', { level: 1, name: '사용자 지정' })).toBeVisible({ timeout: 15_000 })
  const tabs = page.getByRole('tablist', { name: '사용자 지정' })
  await expect(tabs.getByRole('tab', { name: '양식', exact: true })).toHaveAttribute('aria-selected', 'true')
  // One catalogue: no 내 항목 / 탐색 switch, no 추가.
  await expect(page.getByRole('button', { name: '내 항목' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '추가', exact: true })).toHaveCount(0)
  const reports = page.getByRole('region', { name: '양식 · 보고서' })
  await expect(reports).toBeVisible()
  await expect(reports.getByRole('heading', { name: /보고서 양식/ })).toBeVisible()
  await expect(page.getByRole('region', { name: '양식 · 슬라이드' })).toBeVisible()
  await expect(page.getByRole('region', { name: '공유된 디자인' })).toHaveCount(0)
  await expect(page.getByRole('tabpanel')).not.toContainText('서식')
  await shot(page, 'hub-templates', phone)

  // Search narrows the catalogue.
  if (phone) await page.getByRole('button', { name: '검색', exact: true }).click()
  await page.getByRole('searchbox', { name: '검색' }).fill('존재하지 않는 양식')
  await expect(page.getByText('조건에 맞는 양식이 없습니다.')).toBeVisible()
  await page.getByRole('searchbox', { name: '검색' }).fill('')

  // 디자인: the person's designs, and under 탐색 the shared ones, with no templates.
  await tabs.getByRole('tab', { name: '디자인', exact: true }).click()
  await expect(page).toHaveURL(/tab=designs/)
  await expect(page.getByRole('region', { name: '내 디자인' })).toContainText('학과 공식 문서')
  await shot(page, 'hub-designs', phone)
  await page.getByRole('button', { name: '탐색' }).click()
  await expect(page.getByRole('region', { name: '공유된 디자인' })).toContainText('기관 공용')
  await expect(page.getByRole('region', { name: /^양식 · / })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '이 양식으로 시작' })).toHaveCount(0)

  // 「추가」 on 디자인 makes a design, not a 디자인 시스템.
  await page.getByRole('button', { name: '추가', exact: true }).click()
  await expect(page.getByRole('menuitem', { name: '디자인 만들기' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: /디자인 시스템/ })).toHaveCount(0)
  await page.keyboard.press('Escape')

  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('the old template addresses land on the 양식 tab', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/designs?tab=template')
  await expect(page).toHaveURL(/\/customize\?tab=templates$/, { timeout: 15_000 })
  await expect(page.getByRole('region', { name: '양식 · 보고서' })).toBeVisible()
  expect(state.problems).toEqual([])
})
