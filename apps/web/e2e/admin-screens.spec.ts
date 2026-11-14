import { expect, test, type Page } from '@playwright/test'
import { mockApp } from './super-agent-fixture'

/** Admin screens the admin walk-through found wanting: a missing publication, the routing table's
 *  effort column. */

const shots = process.env.SCREEN_SHOTS
async function shot(page: Page, name: string) {
  if (!shots) return
  await page.screenshot({ path: `${shots}/admin/${name}-${page.viewportSize()?.width ?? 1440}.png` })
}

test('a publication that does not exist says so, with the way back to the list', { tag: '@phone' }, async ({ page }) => {
  await mockApp(page)
  await page.route('**/api/admin/publications/**', (route) =>
    route.fulfill({ status: 404, json: { detail: 'publication_not_found' } }))
  await page.goto('/admin/publications/00000000000000000000000000000000')
  await expect(page.getByText('공개 신청을 찾을 수 없습니다')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText(/목록을 새로 불러오지 못했습니다/)).toHaveCount(0)
  await expect(page.getByRole('link', { name: '목록으로' })).toHaveAttribute('href', '/admin/publications')
  await shot(page, 'publication-missing')
})

test('every effort in the routing table reads whole', { tag: '@phone' }, async ({ page }) => {
  await mockApp(page)
  await page.goto('/admin/system/routing')
  const efforts = page.getByLabel(/ · 추론 강도$/)
  await expect(efforts.first()).toBeVisible({ timeout: 15_000 })
  for (const select of await efforts.all()) await select.selectOption('high')
  // the selected label fits the box beside its chevron
  const clipped = await efforts.evaluateAll((nodes) => nodes.filter((node) => {
    const select = node as HTMLSelectElement
    const style = getComputedStyle(select)
    const context = document.createElement('canvas').getContext('2d')!
    context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`
    const text = context.measureText(select.selectedOptions[0]?.text ?? '').width
    const room = select.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
    return text > room + 0.5
  }).length)
  expect(clipped).toBe(0)
  await efforts.first().scrollIntoViewIfNeeded()
  await shot(page, 'routing-effort')
})
