import { expect, test, type Page } from '@playwright/test'
import { signInAs } from './helpers'

/**
 * 보고서와 슬라이드가 같은 말을 쓰는가.
 *
 * Every audit so far measured one screen at a time — is this text clipped, is
 * this contrast enough, is this target big enough — and both panels passed
 * theirs. Consistency is not a property of either panel; it is a property of
 * the pair, and nothing had ever looked at the pair.
 *
 * What a person sees when they do: the same job named 「레이아웃」 on one and
 * nothing on the other, 「검토」 holding 문서 검사 · 근거 here and 문서 검사 ·
 * 검사 there, a count badge on one 보기 tab and a navigation group on the
 * other. Each is defensible alone. Together they read as two products.
 */

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

  // 두 표면이 다 가진 탭은 이름이 같아야 한다. A surface-specific tab is
  // honest — a report has pages, a deck has a slideshow — but a tab both
  // surfaces have has to be called the same thing.
  const shared = ['홈', '삽입', '검토', '보기', '파일']
  for (const tab of shared) {
    expect(Object.keys(report), `보고서에 ${tab} 탭이 없습니다`).toContain(tab)
    expect(Object.keys(deck), `슬라이드에 ${tab} 탭이 없습니다`).toContain(tab)
  }

  // 빈 탭은 없어야 한다 — 눌러 봤더니 아무것도 없는 탭은 탭이 아니다.
  for (const [where, map] of [['보고서', report], ['슬라이드', deck]] as const) {
    for (const [tab, groups] of Object.entries(map)) {
      expect(groups.length, `${where}의 ${tab} 탭이 비어 있습니다`).toBeGreaterThan(0)
    }
  }

  // 같은 일은 같은 탭에 있어야 한다. 저장 시점은 되돌아보는 일이므로 검토에
  // 있고, 내보내기는 파일을 만드는 일이므로 파일에 있다.
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

  // 같은 리본 안의 버튼은 높이와 글자가 같아야 한다. 한쪽이 배지를 감싼
  // 맨 button 을 쓰고 다른 쪽이 공용 Button 을 쓰면 여기서 갈린다.
  const odd = [...deck].filter((one) => !report.has(one))
  expect(odd, '슬라이드 리본에만 있는 버튼 모양').toEqual([])
})
