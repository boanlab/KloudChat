/**
 * The title and the section headings are text somebody can fix.
 *
 * The page view rendered them as plain React text while the paragraphs beside
 * them took typing, so a typo in a heading could be corrected in the web view's
 * Markdown editor and nowhere else. Proven by the round trip, not by the caret
 * appearing.
 */
import { expect, test } from '@playwright/test'
import { artifactReady, signIn } from './helpers'

test('페이지뷰에서 제목과 절 제목을 고칠 수 있다', async ({ page }) => {
  await signIn(page)
  await page.goto('/artifacts')
  // Filtered and opened by its own session, the way the sibling specs learned
  // to. `getByRole('button', { name: /보고/ }).first()` was picking whichever
  // control on the page happened to say 보고 — which on `/artifacts` includes
  // every conversation in the sidebar that ever asked for a report. Worse, the
  // reload below then had no session in the URL to come back to, so this test
  // typed into one document and looked for its mark in another.
  await page.getByRole('tab', { name: /^보고서/ }).click()
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  await artifactReady(page, 30_000)
  // 제목을 눌러 고치는 화면은 「문서 수정」이다 — 「페이지뷰」는 이제 읽기
  // 전용으로 쪽을 나눈 렌더다.
  const edit = page.getByRole('button', { name: '문서 수정' })
  if (await edit.isVisible().catch(() => false)) await edit.click()
  else await page.getByRole('button', { name: '내용 편집' }).click()
  await expect(page.locator('.page').first()).toBeVisible({ timeout: 30_000 })

  const mark = `제목수정-${Date.now()}`
  // A heading that refuses the caret reads as broken in a view that looks like
  // paper and takes typing in the paragraphs beside it.
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
  // The save button is drawn only while there is something unsaved, so its
  // going is the save landing. Reloading straight after the click threw the
  // edit away whenever the API was busy enough to lose the race — which
  // running alone never is, and running after another report spec often is.
  await expect(save).toBeHidden({ timeout: 30_000 })
  await page.reload()
  // Filtered to what a reader can see. The contents are a drawer now and they
  // sit ahead of the document in the DOM, so `.first()` on a section heading
  // picked the closed drawer's copy of it — present, hidden, and not the thing
  // this test is about.
  await expect(page.getByText(mark).filter({ visible: true }).first()).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByText('절수정').filter({ visible: true }).first()).toBeVisible()
})
