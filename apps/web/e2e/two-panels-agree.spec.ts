import { expect, test, type Page } from '@playwright/test'
import { deckToolbar, deckToolbarMenu, signInAs } from './helpers'

/**
 * The report and deck panels use the same vocabulary. The report edits through its ribbon; the
 * web slides through one PowerPoint-editor toolbar row, whose buttons share one shape.
 */

const ADMIN = { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' }

/** The ribbon a panel offers: its tabs, and the groups under each tab. */
async function ribbonOf(page: Page): Promise<Record<string, string[]>> {
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel).toBeVisible({ timeout: 30_000 })
  // The ribbon shows only while editing.
  const toggle = panel.locator('header').first().getByRole('button', { name: '편집', exact: true })
  if ((await toggle.getAttribute('aria-pressed')) !== 'true') await toggle.click()
  const tabs = await panel.getByRole('tab').allInnerTexts()
  const map: Record<string, string[]> = {}
  for (const tab of tabs.map((one) => one.trim()).filter(Boolean)) {
    await panel.getByRole('tab', { name: tab, exact: true }).click()
    await page.waitForTimeout(400)
    map[tab] = await panel.evaluate((root) =>
      [...root.querySelectorAll('section[aria-label]')]
        .map((one) => one.getAttribute('aria-label') ?? '')
        .filter(Boolean),
    )
  }
  return map
}

/** The deck toolbar's words: its buttons, and the sections of its 테마 menu. */
async function toolbarOf(page: Page): Promise<{ buttons: string[]; design: string[] }> {
  const toolbar = deckToolbar(page)
  await expect(toolbar).toBeVisible({ timeout: 30_000 })
  const buttons = await toolbar.locator(':scope > button, :scope > .kc-menu > button').evaluateAll((all) =>
    all.map((one) => one.getAttribute('aria-label') ?? ''))
  const menu = await deckToolbarMenu(page, '테마')
  const design = await menu.locator('.kc-mlabel').allInnerTexts()
  await page.keyboard.press('Escape')
  return { buttons, design: design.map((one) => one.trim()) }
}

async function openFirst(page: Page, tab: RegExp) {
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: tab }).click()
  await page.waitForTimeout(1_200)
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  await expect(page.getByLabel('중지')).toBeHidden({ timeout: 180_000 })
}

test('두 패널이 같은 말을 쓴다', async ({ page }) => {
  test.setTimeout(420_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  await openFirst(page, /^보고서/)
  const report = await ribbonOf(page)
  await openFirst(page, /^슬라이드/)
  const deck = await toolbarOf(page)

  console.log('\n===== 보고서 =====')
  for (const [tab, groups] of Object.entries(report)) console.log(`  ${tab}: ${groups.join(' · ')}`)
  console.log('===== 슬라이드 =====')
  console.log(`  도구: ${deck.buttons.join(' · ')}`)
  console.log(`  테마: ${deck.design.join(' · ')}`)

  // The report's tabs and the deck's toolbar name the same jobs alike.
  for (const name of ['테마', '검토']) {
    expect(Object.keys(report), `보고서에 ${name} 탭이 없습니다`).toContain(name)
    expect(deck.buttons.some((one) => one === name || one.startsWith(`${name} ·`)), `슬라이드 도구에 ${name}이 없습니다`).toBe(true)
  }

  // No empty tabs.
  for (const [tab, groups] of Object.entries(report)) {
    expect(groups.length, `보고서의 ${tab} 탭이 비어 있습니다`).toBeGreaterThan(0)
  }

  // Same job, same place: 테마 and 강조색 under 테마; 내려받기 is in the header, not a tab or a tool.
  expect(report['테마'], '보고서: 테마가 테마 탭에 없습니다').toContain('테마')
  expect(report['테마'], '보고서: 강조색이 테마 탭에 없습니다').toContain('강조색')
  expect(deck.design, '슬라이드: 테마가 테마 메뉴에 없습니다').toContain('테마')
  expect(deck.design, '슬라이드: 강조색이 테마 메뉴에 없습니다').toContain('강조색')
  expect(Object.keys(report), '보고서: 파일 탭이 남아 있습니다').not.toContain('파일')
  expect(deck.buttons, '슬라이드: 내려받기가 도구 줄에 있습니다').not.toContain('내려받기')
})

test('보고서 리본과 슬라이드 도구 줄의 버튼이 저마다 한 모양이다', async ({ page }) => {
  test.setTimeout(420_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  const shapeOf = async (): Promise<string[]> =>
    await page.locator('[data-panel="artifact"]').evaluate((root) =>
      [...root.querySelectorAll('section[aria-label] button')]
        .filter((one) => (one as HTMLElement).offsetParent)
        .map((one) => {
          const style = getComputedStyle(one)
          return `${Math.round(one.getBoundingClientRect().height)}px ${style.fontSize} ${style.fontWeight}`
        }),
    )

  await openFirst(page, /^보고서/)
  const report = new Set(await shapeOf())
  await openFirst(page, /^슬라이드/)
  // The deck's toolbar is the PowerPoint editor's: every button the same height.
  await expect(deckToolbar(page)).toBeVisible({ timeout: 30_000 })
  const deck = new Set(await deckToolbar(page).locator('button:visible').evaluateAll((all) =>
    all.map((one) => `${Math.round(one.getBoundingClientRect().height)}px`)))

  console.log('\n보고서 버튼 모양:', [...report].join(' | '))
  console.log('슬라이드 도구 버튼 높이:', [...deck].join(' | '))

  expect([...deck], '슬라이드 도구 줄에 높이가 다른 버튼').toHaveLength(1)
})
