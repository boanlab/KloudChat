import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

const DECK = process.env.KC_DECK || ''
const REPORT = process.env.KC_REPORT || ''
const TAG = process.env.KC_TAG || 'after'
const CLIP = { x: 800, y: 60, width: 800, height: 940 }

test.use({ viewport: { width: 1600, height: 1000 } })

test('deck slides with figures', async ({ page }) => {
  test.skip(!DECK, 'no deck session')
  await signInAs(page, 'test@kloud.zone', process.env.KC_PASSWORD || '')
  await page.goto(`/s/${DECK}`)
  await expect(page.getByRole('button', { name: '다음 장' })).toBeVisible({ timeout: 30_000 })
  const wanted = (process.env.KC_SLIDES || '3,4,5').split(',').map(Number)
  for (let n = 1; n <= Math.max(...wanted); n += 1) {
    if (wanted.includes(n)) {
      // Give mermaid time to draw and the raster to be stored.
      await page.waitForTimeout(2500)
      await page.screenshot({ path: `test-results/${TAG}-deck-${n}.png`, clip: CLIP })
    }
    await page.getByRole('button', { name: '다음 장' }).click()
  }
})

test('report sections with figures', async ({ page }) => {
  test.skip(!REPORT, 'no report session')
  await signInAs(page, 'test@kloud.zone', process.env.KC_PASSWORD || '')
  await page.goto(`/s/${REPORT}`)
  await page.waitForTimeout(6000)
  await page.screenshot({ path: `test-results/${TAG}-report-top.png`, clip: CLIP })
  // Each drawn figure sits above its `그림:` caption; scroll so the caption ends the view.
  const captions = page.getByText(/^그림: /)
  const count = await captions.count()
  for (let i = 0; i < Math.min(count, 3); i += 1) {
    await captions.nth(i).evaluate((el) => el.scrollIntoView({ block: 'end' }))
    await page.waitForTimeout(800)
    await page.screenshot({ path: `test-results/${TAG}-report-${i + 1}.png`, clip: CLIP })
  }
  // With no captions, show the middle of the document instead.
  if (count === 0) {
    await page.mouse.wheel(0, 900)
    await page.waitForTimeout(600)
    await page.screenshot({ path: `test-results/${TAG}-report-1.png`, clip: CLIP })
  }
  console.log('report figures:', count)
})
