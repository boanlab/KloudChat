import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('what the version dialog looks like from the artifacts route', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^보고서/ }).click()
  await page.getByText('원본 작업 열기').first().click()
  await page.waitForURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  const history = page.getByRole('button', { name: '버전 기록' })
  console.log('버전 기록 buttons:', await history.count())
  await expect(history).toBeVisible({ timeout: 20_000 })
  await history.click()
  await page.waitForTimeout(1500)
  console.log('dialogs on page:', await page.getByRole('dialog').count())
  for (let i = 0; i < (await page.getByRole('dialog').count()); i++) {
    const d = page.getByRole('dialog').nth(i)
    console.log(`  dialog[${i}] aria-label=`, await d.getAttribute('aria-label'))
    console.log(`  dialog[${i}] text=`, ((await d.textContent()) ?? '').slice(0, 120).replace(/\s+/g, ' '))
  }
  console.log('현재 v match:', await page.getByText(/현재 v\d+/).count())
  console.log('empty line match:', await page.getByText('아직 저장된 이전 판이 없습니다.').count())
  console.log('되돌리기 buttons:', await page.getByRole('button', { name: /되돌리기/ }).count())
})
