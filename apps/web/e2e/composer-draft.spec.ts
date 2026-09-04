import { expect, test } from '@playwright/test'
import { signIn, surfaceOn } from './helpers'

/** Picking a 서식 or switching surface never overwrites a draft. Image requests stubbed. */
test('서식을 고르면 쓰던 문장 뒤에 붙고, 덮어쓰지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.route('**/api/sessions/*/images', (route) => route.fulfill({ json: [] }))

  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
  const composer = page.getByLabel('프롬프트 입력')
  const mine = '골목 어귀에서 손을 흔드는 아이. 늦은 오후 빛으로.'
  await composer.fill(mine)

  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  // Searched: the gallery pages.
  await dialog.getByPlaceholder(/검색/).fill('포스터')
  const search = dialog.getByLabel(/서식 검색|시작점 검색/)
  if (await search.count()) await search.fill('포스터')
  const poster = dialog.locator('div.group', { hasText: '포스터' }).first()
  await expect(poster).toBeVisible({ timeout: 20_000 })
  await poster.getByRole('button', { name: '이 서식으로 시작' }).click()

  // The draft stays; the 서식's questions open above the box.
  await expect(composer).toHaveValue(mine)
  await expect(page.getByRole('group', { name: '포스터 시작점 질문' })).toBeVisible()
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
