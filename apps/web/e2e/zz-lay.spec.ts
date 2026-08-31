import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

for (const [kind, label] of [['chat', 'chat'], ['report', 'report']] as const) {
  test(`layout-${label}`, async ({ page }) => {
    await signIn(page)
    await page.goto(`/new/${kind}`)
    await page.getByRole('button', { name: '서식 고르기' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.waitForTimeout(2500)
    const widths = await page
      .getByRole('dialog')
      .locator('.grid > *')
      .evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().width)))
    console.log(`${label} 칸 너비>>`, JSON.stringify([...new Set(widths)]), '칸', widths.length)
    const inner = await page
      .getByRole('dialog')
      .locator('.grid > div > button, .grid > button')
      .evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().width)))
    console.log(`${label} 카드 너비>>`, JSON.stringify([...new Set(inner)]))
    await page.getByRole('dialog').screenshot({ path: `/w/lay-${label}.png` })
  })
}
