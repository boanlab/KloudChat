import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/** Templates an administrator provides to the whole instance: added, found in the gallery, edited, removed. */
test('관리자가 등록한 공용 템플릿이 갤러리에 함께 보이고, 자리에서 고쳐진다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)
  await page.goto('/admin/system/templates')

  // Scoped to the section: the form inside it has its own 이름 and 저장.
  const section = page.getByRole('region', { name: '공용 템플릿' })
  const title = `기관 공문 ${Date.now()}`
  await section.getByRole('button', { name: '공용 템플릿 추가' }).click()
  // The surface picker exists only here.
  await section.getByRole('button', { name: '보고서', exact: true }).click()
  await section.getByLabel('이름', { exact: true }).fill(title)
  await section.getByLabel('설명').fill('기관 표준 공문 양식')
  await section.getByLabel('준비물').fill('수신처, 제목')
  await section.getByLabel('문구').fill('기관 공문 양식에 맞춰 써 줘.\n\n수신: ')
  await section.getByRole('button', { name: '저장', exact: true }).click()

  const row = section.locator('li', { hasText: title })
  await expect(row).toBeVisible({ timeout: 20_000 })
  // The surface is on the row.
  await expect(row.getByText('보고서')).toBeVisible()

  // In the gallery, marked as shared.
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  // Searched: the gallery pages.
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(title)
  const card = page.getByRole('dialog').locator('.grid > *').filter({ hasText: title })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card).toContainText('공용')

  // By accessible name: every card's visible button text is the same.
  await page.getByRole('button', { name: `${title} 시작점 선택` }).click()
  await expect(page.getByRole('button', { name: `${title} 시작점 해제` })).toBeVisible()

  // Edited from the same screen.
  await page.goto('/admin/system/templates')
  await section.getByRole('button', { name: `${title} 수정` }).click()
  await expect(section.getByLabel('이름', { exact: true })).toHaveValue(title)
  const fixed = `${title} (개정)`
  await section.getByLabel('이름', { exact: true }).fill(fixed)
  await section.getByRole('button', { name: '저장', exact: true }).click()
  await expect(section.locator('li', { hasText: fixed })).toBeVisible({ timeout: 20_000 })
  // Still shared after the edit.
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  await page.getByLabel(/시작점 검색|결과 서식 검색/).fill(fixed)
  // `filter`, not a name regex: "(개정)" contains regex metacharacters.
  const revised = page.getByRole('dialog').locator('.grid > *').filter({ hasText: fixed })
  await expect(revised).toBeVisible({ timeout: 20_000 })
  await expect(revised).toContainText('공용')

  await page.goto('/admin/system/templates')
  await section.getByRole('button', { name: `${fixed} 삭제` }).click()
  await expect(section.locator('li', { hasText: fixed })).toHaveCount(0, { timeout: 15_000 })
})
