import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

const REPORT = process.env.KC_REPORT || ''
const TAG = process.env.KC_TAG || 'edit'
const CLIP = { x: 800, y: 60, width: 800, height: 940 }

test.use({ viewport: { width: 1600, height: 1000 } })

test('the edit tab opens the page editor and home puts the web view back', async ({ page }) => {
  test.skip(!REPORT, 'no report session')
  await signInAs(page, 'test@kloud.zone', process.env.KC_PASSWORD || '')
  await page.goto(`/s/${REPORT}`)
  await page.waitForTimeout(4000)
  await page.getByRole('tab', { name: '편집' }).or(page.getByRole('button', { name: '편집', exact: true })).first().click()
  await page.waitForTimeout(3500)
  await page.screenshot({ path: `test-results/${TAG}-edit-top.png`, clip: CLIP })
  await page.mouse.wheel(0, 900)
  await page.waitForTimeout(600)
  await page.screenshot({ path: `test-results/${TAG}-edit-page2.png`, clip: CLIP })
  await page.getByRole('tab', { name: '홈' }).or(page.getByRole('button', { name: '홈', exact: true })).first().click()
  await page.waitForTimeout(2500)
  await page.screenshot({ path: `test-results/${TAG}-home-after.png`, clip: CLIP })
  await expect(page.getByRole('button', { name: '페이지뷰' })).toBeVisible({ timeout: 5000 })
})
