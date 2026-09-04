/** The title and section headings are editable in 문서 수정, proven by the round trip. */
import { expect, test } from '@playwright/test'
import { artifactReady, signIn } from './helpers'

test('페이지뷰에서 제목과 절 제목을 고칠 수 있다', async ({ page }) => {
  await signIn(page)
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^보고서/ }).click()
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  await artifactReady(page, 30_000)
  // 문서 수정 edits in place; 페이지뷰 is read-only.
  const edit = page.getByRole('button', { name: '문서 수정' })
  if (await edit.isVisible().catch(() => false)) await edit.click()
  else await page.getByRole('button', { name: '내용 편집' }).click()
  await expect(page.locator('.page').first()).toBeVisible({ timeout: 30_000 })

  const mark = `제목수정-${Date.now()}`
  const h1 = page.locator('.page h1').first()
  await expect(h1).toHaveAttribute('contenteditable', 'true')
  await h1.click()
  await page.keyboard.press('End')
  await page.keyboard.type(` ${mark}`)
  await page.keyboard.press('Enter')

  const h2 = page.locator('.page h2').first()
  await expect(h2).toHaveAttribute('contenteditable', 'true')
  await h2.click()
  await page.keyboard.press('End')
  await page.keyboard.type(' 절수정')
  await page.keyboard.press('Enter')

  const save = page.getByRole('button', { name: '저장', exact: true })
  await save.click()
  // The save button exists only while something is unsaved; its going is the save landing.
  await expect(save).toBeHidden({ timeout: 30_000 })
  await page.reload()
  // Visible only: the closed contents drawer holds a hidden copy of each heading.
  await expect(page.getByText(mark).filter({ visible: true }).first()).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByText('절수정').filter({ visible: true }).first()).toBeVisible()
})
