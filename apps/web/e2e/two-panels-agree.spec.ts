import { expect, test, type Page } from '@playwright/test'
import { signInAs } from './helpers'

/** The report and deck panels use the same ribbon vocabulary and button shapes. */

const ADMIN = { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' }

/** The ribbon a panel offers: its tabs, and the groups under each tab. */
async function ribbonOf(page: Page): Promise<Record<string, string[]>> {
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel).toBeVisible({ timeout: 30_000 })
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

async function openFirst(page: Page, tab: RegExp) {
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: tab }).click()
  await page.waitForTimeout(1_200)
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  await expect(page.getByLabel('중지')).toBeHidden({ timeout: 180_000 })
}

test('두 패널의 리본이 같은 말을 쓴다', async ({ page }) => {
  test.setTimeout(420_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  await openFirst(page, /^보고서/)
  const report = await ribbonOf(page)
  await openFirst(page, /^슬라이드/)
  const deck = await ribbonOf(page)

  console.log('\n===== 보고서 =====')
  for (const [tab, groups] of Object.entries(report)) console.log(`  ${tab}: ${groups.join(' · ')}`)
  console.log('===== 슬라이드 =====')
  for (const [tab, groups] of Object.entries(deck)) console.log(`  ${tab}: ${groups.join(' · ')}`)

  // Tabs both surfaces have must share a name; surface-specific tabs are fine.
  const shared = ['홈', '삽입', '검토', '보기', '파일']
  for (const tab of shared) {
    expect(Object.keys(report), `보고서에 ${tab} 탭이 없습니다`).toContain(tab)
    expect(Object.keys(deck), `슬라이드에 ${tab} 탭이 없습니다`).toContain(tab)
  }

  // No empty tabs.
  for (const [where, map] of [['보고서', report], ['슬라이드', deck]] as const) {
    for (const [tab, groups] of Object.entries(map)) {
      expect(groups.length, `${where}의 ${tab} 탭이 비어 있습니다`).toBeGreaterThan(0)
    }
  }

  // Same job, same tab: 버전 under 검토, 내보내기 under 파일.
  for (const [where, map] of [['보고서', report], ['슬라이드', deck]] as const) {
    expect(map['검토'], `${where}: 저장 시점이 검토에 없습니다`).toContain('버전')
    expect(map['파일'], `${where}: 내보내기가 파일에 없습니다`).toContain('내보내기')
    expect(map['파일'], `${where}: 저장 시점이 아직 파일에 있습니다`).not.toContain('버전')
  }
})

test('두 패널의 리본 버튼이 같은 모양이다', async ({ page }) => {
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
  const deck = new Set(await shapeOf())

  console.log('\n보고서 버튼 모양:', [...report].join(' | '))
  console.log('슬라이드 버튼 모양:', [...deck].join(' | '))

  // Buttons in the same ribbon share height and type.
  const odd = [...deck].filter((one) => !report.has(one))
  expect(odd, '슬라이드 리본에만 있는 버튼 모양').toEqual([])
})
