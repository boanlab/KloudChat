import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

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
