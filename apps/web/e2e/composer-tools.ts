import type { Page } from '@playwright/test'

/** Opens the composer's 「+」 menu and picks `item` from it. */
export async function openTool(page: Page, item: string | RegExp) {
  await page.getByRole('button', { name: '도구', exact: true }).click()
  await page.getByRole('menuitem', { name: item }).click()
}

/** Sets web search through 「+」 › 웹 검색. */
export async function setWebSearch(page: Page, mode: '자동' | '항상' | '끔') {
  await openTool(page, /^웹 검색/)
  await page.getByRole('menuitemradio', { name: new RegExp(`^${mode}`) }).click()
}

/**
 * Opens the 템플릿 gallery: the 「템플릿」 chip beside 형식 when a report or deck has nothing
 * sent yet, else 「+」 › 템플릿 고르기.
 */
export async function openTemplates(page: Page) {
  const chip = page.getByTestId('template-chip')
  if (await chip.isVisible().catch(() => false)) return chip.click()
  await openTool(page, '템플릿 고르기')
}

/** The rows the composer's 「+」 menu offers (by accessible name); the menu is closed again. */
export async function toolNames(page: Page): Promise<string[]> {
  const plus = page.getByRole('button', { name: '도구', exact: true })
  if ((await plus.count()) === 0) return []
  await plus.first().click()
  const menu = page.getByRole('menu', { name: '도구' })
  await menu.waitFor()
  const names = await menu.getByRole('menuitem').evaluateAll((items) =>
    items.map((item) => item.getAttribute('aria-label') ?? item.textContent?.trim() ?? ''),
  )
  await page.keyboard.press('Escape')
  await menu.waitFor({ state: 'detached' })
  return names
}
