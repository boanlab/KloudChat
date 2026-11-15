import { expect, test, type Page } from '@playwright/test'
import { mockApp, model } from './super-agent-fixture'

/** Admin screens the admin walk-through found wanting: a missing publication, the routing table's
 *  effort column. */

const shots = process.env.SCREEN_SHOTS
/** The whole page at once: the app scrolls inside its own panes, so the window grows to fit them. */
async function wholePage(page: Page, path: string) {
  const size = page.viewportSize()!
  const height = await page.evaluate(() => Math.max(
    document.documentElement.scrollHeight,
    ...[...document.querySelectorAll('*')].filter((el) => {
      const style = getComputedStyle(el)
      return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight > el.clientHeight + 4
    }).map((el) => el.scrollHeight + el.getBoundingClientRect().top),
  ))
  await page.setViewportSize({ width: size.width, height: Math.min(Math.ceil(height) + 40, 5000) })
  await page.waitForTimeout(300)
  await page.screenshot({ path })
  await page.setViewportSize(size)
}
async function shot(page: Page, name: string, target?: import('@playwright/test').Locator | 'full') {
  if (!shots) return
  const path = `${shots}/admin/${name}-${page.viewportSize()?.width ?? 1440}.png`
  if (target === 'full') return void (await wholePage(page, path))
  if (target) return void (await target.screenshot({ path }))
  await page.screenshot({ path })
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
  // nothing of the table outside the screen: a phone stacks each row as a card
  const table = page.getByTestId('super-agent-table')
  const width = page.viewportSize()!.width
  const box = (await table.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(width)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  if (width < 640) await expect(table.locator('td span', { hasText: 'Super Agent (strict-local)' }).first()).toBeVisible()
  await shot(page, 'routing-effort', 'full')
})

test('the routing table shows each model name whole, its price on the line under it', { tag: '@phone' }, async ({ page }) => {
  await mockApp(page, 'manual', undefined, undefined, {
    catalogue: [
      // as the gateway lists them: the label with the vendor, the name without
      { ...model('local/flash', 'Qwen · Qwen3.8 Flash (local)', 'hybrid'), name: 'Qwen3.8 Flash (local)' },
      { ...model('strict-local/flash', 'DeepSeek · Deepseek V4 Flash (strict-local)', 'self_hosted'), name: 'Deepseek V4 Flash (strict-local)' },
      { ...model('external/deep', 'DeepSeek · Deepseek V4.1 Flash', 'external', 4), name: 'Deepseek V4.1 Flash' },
    ],
  })
  const widths = (page.viewportSize()?.width ?? 0) < 640 ? [390, 360] : [1440, 1280]
  for (const width of widths) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/admin/system/routing')
    const selects = page.getByTestId('super-agent-table').getByLabel(/ · (Super Agent|strict-local) 모델$/)
    await expect(selects.first()).toBeVisible({ timeout: 15_000 })
    const clipped = await selects.evaluateAll((nodes) => nodes.flatMap((node) => {
      const select = node as HTMLSelectElement
      const style = getComputedStyle(select)
      const context = document.createElement('canvas').getContext('2d')!
      context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`
      const text = select.selectedOptions[0]?.text ?? ''
      const room = select.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
      return context.measureText(text).width > room + 0.5 ? [`${select.getAttribute('aria-label')}: ${text}`] : []
    }))
    expect(clipped).toEqual([])
    // the price is not in the box but under it
    await expect(selects.first().locator('option:checked')).not.toContainText('크레딧')
    await expect(page.getByTestId('model-rate').first()).toBeVisible()
    await shot(page, `routing-models-${width}`, 'full')
  }
})
