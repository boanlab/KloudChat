import { expect, test } from '@playwright/test'
import { signIn } from './helpers'
import { openTemplates } from './composer-tools'

/** Picking a 서식 or switching surface never overwrites a draft. */
test('서식을 고르면 쓰던 문장은 그대로 두고 칩만 붙는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')

  const composer = page.getByLabel('프롬프트 입력')
  const mine = '3월 운영위원회 회의 내용을 정리해 줘.'
  await composer.fill(mine)

  await openTemplates(page)
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  // Searched: the gallery pages.
  await dialog.getByLabel('양식 검색').fill('회의록')
  const minutes = dialog.locator('div.group', { hasText: '회의록' }).first()
  await expect(minutes).toBeVisible({ timeout: 20_000 })
  await minutes.getByRole('button', { name: '이 양식으로 시작' }).click()

  // The draft stays; the 서식 is a chip.
  await expect(composer).toHaveValue(mine)
  await expect(page.getByRole('button', { name: '회의록 양식 해제' })).toBeVisible()
})

/** A draft belongs to the surface it was typed on; a document surface's box starts empty. */
test('표면을 옮겨도 챗 초안은 그대로 있고, 문서 입력창은 비어 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/chat')

  const composer = page.getByLabel('프롬프트 입력')
  const mine = '3월 정기 점검 결과를 정리해 줘.'
  await composer.fill(mine)

  // The chip switches the surface in place, so the check is on the composer, not the address.
  await page.getByRole('button', { name: '보고서', exact: true }).first().click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')

  // And back.
  await page.getByRole('button', { name: '챗', exact: true }).first().click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(mine)
})
