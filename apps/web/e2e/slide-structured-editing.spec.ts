import { expect, test } from '@playwright/test'
import { deckLayout, deckPanel, deckThumbs, deckTitle, deckToolbarMenu, saveDeck, signIn } from './helpers'

test('슬라이드를 복제하고 내용을 유지한 채 레이아웃을 바꾼다', async ({ page }) => {
  test.setTimeout(90_000)
  await signIn(page)
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 20_000 })

  // One numbered thumbnail per slide.
  await expect(deckThumbs(page).first()).toBeVisible({ timeout: 20_000 })
  const before = await deckThumbs(page).count()
  await deckThumbs(page).first().click()
  const title = await deckTitle(page).innerText().catch(() => '')

  // 슬라이드 복제 from the slide menu puts the copy after the slide and shows it.
  await deckThumbs(page).first().click({ button: 'right' })
  await page.getByRole('menu', { name: '슬라이드 메뉴' }).getByRole('menuitem', { name: '슬라이드 복제' }).click()
  await expect(deckThumbs(page)).toHaveCount(before + 1)
  await expect(deckThumbs(page).nth(1)).toHaveAttribute('aria-current', 'true')
  await expect(deckTitle(page)).toContainText('사본')

  // A layout change keeps the words.
  await deckLayout(page, '카드')
  if (title) await expect(deckPanel(page).getByText(title, { exact: false }).first()).toBeVisible()
  const layouts = await deckToolbarMenu(page, '레이아웃')
  await expect(layouts.getByRole('menuitemradio', { name: '카드', exact: true })).toHaveAttribute('aria-checked', 'true')
  await page.keyboard.press('Escape')

  // Both edits go to the server with one 저장.
  await saveDeck(page)
  await expect(deckPanel(page).getByText('그사이 다른 판이 저장되어 저장하지 않았습니다.')).toHaveCount(0)

  await page.reload()
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 20_000 })
  await expect(deckThumbs(page)).toHaveCount(before + 1, { timeout: 20_000 })
  await deckThumbs(page).nth(1).click()
  const again = await deckToolbarMenu(page, '레이아웃')
  await expect(again.getByRole('menuitemradio', { name: '카드', exact: true })).toHaveAttribute('aria-checked', 'true')
})
