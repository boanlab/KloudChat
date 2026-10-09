import { expect, test } from '@playwright/test'
import { signIn } from './helpers'
import { openTemplates } from './composer-tools'

/** The 양식 catalogue as its own hub tab (`/customize?tab=templates`). Nothing is generated. */

test('디자인과 양식은 사용자 지정의 두 탭으로 나뉜다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/designs')
  const tabs = page.getByRole('tablist', { name: '사용자 지정' })
  await expect(tabs.getByRole('tab', { name: '디자인', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
    { timeout: 20_000 },
  )

  await tabs.getByRole('tab', { name: '양식', exact: true }).click()
  // In the address, so it can be linked.
  await expect(page).toHaveURL(/\/customize\?tab=templates/)

  // Shapes for two surfaces on one screen, grouped by surface.
  const documents = page.getByRole('region', { name: '양식 · 보고서' })
  const decks = page.getByRole('region', { name: '양식 · 슬라이드' })
  await expect(documents.getByText('회의록', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(decks.getByText('제안 덱', { exact: true })).toBeVisible()

  await expect(decks.getByText('회의록', { exact: true })).toHaveCount(0)

  const catalogueSearch = documents.getByLabel('양식 검색')
  if (await catalogueSearch.count()) await catalogueSearch.fill('회의록')
  const minutes = documents.locator('div.group', { hasText: '회의록' })
  await expect(minutes).toHaveCount(1)
  await expect(minutes.getByText('일시와 참석자')).toBeVisible()
  // The checks list is folded, with a count.
  const checks = minutes.locator('summary')
  await expect(checks).toHaveText(/확인하는 것 \d+개/)
  await expect(minutes.locator('li').first()).toBeHidden()
  await checks.click()
  await expect(minutes.locator('li').first()).toBeVisible()

  // Starts the surface with the chip, and an empty composer.
  await minutes.getByRole('button', { name: '이 양식으로 시작' }).click()
  await expect(page).toHaveURL(/\/new\/report/, { timeout: 20_000 })
  await expect(page.getByText('회의록', { exact: true })).toBeVisible()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
})

test('양식 탭은 주소로 바로 열리고, 홈의 줄은 몇 개만 보여 준 뒤 넘긴다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/designs?tab=template')
  await expect(page.getByRole('tab', { name: '양식', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
    { timeout: 20_000 },
  )
  // Counted by the card's action button, not markup structure.
  const anyCard = page.getByRole('button', { name: '이 양식으로 시작', exact: true })
  await expect(anyCard.first()).toBeVisible({ timeout: 20_000 })
  const all = await anyCard.count()
  expect(all).toBeGreaterThan(6)

  // The composer's picker offers only the surface's own 서식.
  await page.goto('/new/slides')
  await openTemplates(page)
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  const offered = dialog.getByRole('button', { name: '이 양식으로 시작', exact: true })
  await expect(offered.first()).toBeVisible({ timeout: 20_000 })
  const shown = await offered.count()
  expect(shown).toBeGreaterThan(0)
  expect(shown).toBeLessThan(all)
  await page.keyboard.press('Escape')

  // The whole catalogue is on its own screen.
  await page.goto('/designs?tab=template')
  await expect(
    page.getByRole('button', { name: '이 양식으로 시작', exact: true }),
  ).toHaveCount(all, { timeout: 20_000 })
})
