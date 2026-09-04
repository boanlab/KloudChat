import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** The 서식 catalogue as its own screen (`/designs?tab=template`). Nothing is generated. */

test('디자인 화면은 만드는 것과 제품이 주는 것을 탭으로 나눠 놓는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/designs')
  const tabs = page.getByRole('tablist')
  await expect(tabs.getByRole('tab', { name: '디자인 시스템', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(page.getByRole('region', { name: '디자인 시스템' })).toBeVisible({
    timeout: 20_000,
  })

  await tabs.getByRole('tab', { name: '서식', exact: true }).click()
  // In the address, so it can be linked.
  await expect(page).toHaveURL(/\/designs\?tab=template/)

  // Shapes for two surfaces on one screen, grouped by surface.
  const documents = page.getByRole('region', { name: '보고서' })
  const decks = page.getByRole('region', { name: '슬라이드' })
  await expect(documents.getByText('회의록', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(decks.getByText('제안 덱', { exact: true })).toBeVisible()

  await expect(decks.getByText('회의록', { exact: true })).toHaveCount(0)

  const catalogueSearch = documents.getByLabel(/서식 검색|시작점 검색/)
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
  await minutes.getByRole('button', { name: '이 서식으로 시작' }).click()
  await expect(page).toHaveURL(/\/new\/report/, { timeout: 20_000 })
  await expect(page.getByText('회의록', { exact: true })).toBeVisible()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
})

test('서식 탭은 주소로 바로 열리고, 홈의 줄은 몇 개만 보여 준 뒤 넘긴다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/designs?tab=template')
  await expect(page.getByRole('tab', { name: '서식', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
    { timeout: 20_000 },
  )
  // Counted by the card's action button, not markup structure.
  const anyCard = page.getByRole('button', { name: '이 서식으로 시작', exact: true })
  await expect(anyCard.first()).toBeVisible({ timeout: 20_000 })
  const all = await anyCard.count()
  expect(all).toBeGreaterThan(6)

  // 작업 시작하기 offers jobs; each carries its shape.
  await page.goto('/new/slides')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  const offered = dialog.getByRole('button', { name: /시작점 선택$/ })
  await expect(offered.first()).toBeVisible({ timeout: 20_000 })
  const shown = await offered.count()
  expect(shown).toBeGreaterThan(0)
  expect(shown).toBeLessThan(all)
  await page.keyboard.press('Escape')

  // The whole catalogue is on its own screen.
  await page.goto('/designs?tab=template')
  await expect(
    page.getByRole('button', { name: '이 서식으로 시작', exact: true }),
  ).toHaveCount(all, { timeout: 20_000 })
})
