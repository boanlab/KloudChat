import { expect, test, type Page } from '@playwright/test'
import { hubFixture } from './customize-fixture'
import { openTool, toolNames } from './composer-tools'

/**
 * 템플릿 is a document's structure (the shipped report and slide templates, formerly 양식), 테마 is
 * its look (formerly 디자인). A template is picked in the composer's 「+ › 템플릿 고르기」 and in the
 * agent form's 결과물; a theme in the agent form and the panel, and themes are kept in 설정 › 테마.
 * Neither is a hub tab. Fixture data only; NAMING_SHOTS (a directory) also saves screenshots.
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

test('the composer picks a 템플릿, and the gallery says so', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const phone = testInfo.project.name === 'phone'
  await page.goto('/new/report')
  // ▦ lives in 「+」 (a bottom sheet on a phone).
  await expect(page.getByRole('button', { name: '도구', exact: true })).toBeVisible({ timeout: 15_000 })
  const names = await toolNames(page)
  expect(names).toContain('템플릿 고르기')
  expect(names).not.toContain('양식 고르기')
  expect(names).not.toContain('서식 고르기')
  await openTool(page, '템플릿 고르기')

  const dialog = page.getByRole('dialog', { name: '템플릿 고르기' })
  await expect(dialog).toBeVisible({ timeout: 15_000 })
  await expect(dialog.getByRole('heading', { name: '템플릿 고르기' })).toBeVisible()
  await expect(dialog.getByLabel('템플릿 검색')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '이 템플릿으로 시작' }).first()).toBeVisible()
  await expect(dialog).not.toContainText(/서식|양식/)
  // The gallery is the catalogue: no hub tab to send people to.
  await expect(dialog.getByRole('link', { name: '모두 보기' })).toHaveCount(0)
  await shot(page, 'gallery', phone)
  expect(state.problems).toEqual([])
})

test('the agent form picks a 템플릿 and a 테마; themes are kept in 설정 › 테마', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  const phone = testInfo.project.name === 'phone'
  await page.goto('/customize/agents/new')
  const form = page.getByTestId('agent-form')
  await expect(form).toBeVisible({ timeout: 15_000 })
  const output = page.getByTestId('agent-output-group')
  await output.locator('summary').click()
  await expect(output.getByRole('combobox', { name: '템플릿' })).toBeVisible()
  await expect(output.getByRole('combobox', { name: '테마' })).toBeVisible()
  await expect(output).not.toContainText(/양식|디자인/)
  await shot(page, 'agent-output', phone)
  await output.getByRole('link', { name: '테마 관리' }).click()
  await expect(page).toHaveURL(/\/settings\/themes$/)
  await expect(page.getByRole('tab', { name: '테마' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('region', { name: '내 테마' })).toContainText('학과 공식 문서')
  await page.getByRole('button', { name: '공유된 테마' }).click()
  await expect(page.getByRole('region', { name: '공유된 테마' })).toContainText('기관 공용')
  await shot(page, 'settings-themes', phone)
  expect(state.problems).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('the old template and design addresses land where they went', async ({ page }, testInfo) => {
  const state = await hubFixture(page, testInfo)
  await page.goto('/designs?tab=template')
  await expect(page).toHaveURL(/\/settings\/themes$/, { timeout: 15_000 })
  await page.goto('/customize?tab=templates')
  await expect(page).toHaveURL(/\/customize\?tab=agents$/, { timeout: 15_000 })
  expect(state.problems).toEqual([])
})
