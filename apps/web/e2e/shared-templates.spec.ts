import { test, expect } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Templates an administrator provides to the whole instance.
 *
 * Anybody can write one for themselves from the gallery. This is the other
 * case: an organisation's own form, entered once and offered to every account.
 */
test('관리자가 등록한 공용 템플릿이 갤러리에 함께 보이고, 자리에서 고쳐진다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)
  await page.goto('/admin/system')

  // Scoped: three inputs on this screen answer to "이름", and two sections
  // besides this one have a 저장 button.
  const section = page.getByRole('region', { name: '공용 템플릿' })
  const title = `기관 공문 ${Date.now()}`
  await section.getByRole('button', { name: '공용 템플릿 추가' }).click()
  // The surface picker only exists here — the gallery already knows which one
  // it is opening from.
  await section.getByRole('button', { name: '보고서', exact: true }).click()
  await section.getByLabel('이름', { exact: true }).fill(title)
  await section.getByLabel('설명').fill('기관 표준 공문 양식')
  await section.getByLabel('준비물').fill('수신처, 제목')
  await section.getByLabel('문구').fill('기관 공문 양식에 맞춰 써 줘.\n\n수신: ')
  await section.getByRole('button', { name: '저장', exact: true }).click()

  const row = section.locator('li', { hasText: title })
  await expect(row).toBeVisible({ timeout: 20_000 })
  // The surface it starts is on the row: a shared list mixing report and
  // slides templates is unreadable without it.
  await expect(row.getByText('보고서')).toBeVisible()

  // It reaches the gallery, where it is marked as everybody's.
  await page.goto('/new/report')
  await page.getByRole('button', { name: '템플릿에서 시작' }).click()
  const card = page.getByRole('dialog').locator('div.group', { hasText: title })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card.getByText('공용')).toBeVisible()

  // Picking it works like any other card.
  await card.getByRole('button').first().click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(/수신:/)

  // Corrected from the same screen it was added on. This is the whole point of
  // sharing rather than copying: one edit, and every account that has not
  // started yet gets the right form.
  await page.goto('/admin/system')
  await section.getByRole('button', { name: `${title} 수정` }).click()
  await expect(section.getByLabel('이름', { exact: true })).toHaveValue(title)
  const fixed = `${title} (개정)`
  await section.getByLabel('이름', { exact: true }).fill(fixed)
  await section.getByRole('button', { name: '저장', exact: true }).click()
  await expect(section.locator('li', { hasText: fixed })).toBeVisible({ timeout: 20_000 })
  // Still shared, not quietly turned private by the edit.
  await page.goto('/new/report')
  await page.getByRole('button', { name: '템플릿에서 시작' }).click()
  const revised = page.getByRole('dialog').locator('div.group', { hasText: fixed })
  await expect(revised).toBeVisible({ timeout: 20_000 })
  await expect(revised.getByText('공용')).toBeVisible()

  // Removed from the same screen it was added on.
  await page.goto('/admin/system')
  await section.getByRole('button', { name: `${fixed} 삭제` }).click()
  await expect(section.locator('li', { hasText: fixed })).toHaveCount(0, { timeout: 15_000 })
})
