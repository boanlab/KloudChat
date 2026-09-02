import { expect, test } from '@playwright/test'
import { signIn, surfaceOn } from './helpers'

/**
 * What the gallery is allowed to do to a sentence somebody is in the middle of.
 *
 * Opening the gallery is a question — "what does this shape do?" — and until
 * now the answer arrived by wiping the box. On the picture and clip surfaces
 * that is the whole of the work: the example sentence is the prompt there, so
 * the thing it wrote over was the prompt too, silently and with nothing to
 * press to get it back.
 *
 * Nothing here makes a picture. The request is stubbed for the same reason
 * `design-chip.spec.ts` stubs it — a picture costs real credits and none of
 * this is about the picture.
 */
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
  // Searched rather than scrolled to. The picture surface carries six 서식
  // since the research figures joined it, so 포스터 is on the second page and
  // a locator that only looks at what is drawn finds nothing.
  await dialog.getByPlaceholder(/검색/).fill('포스터')
  const search = dialog.getByLabel(/서식 검색|시작점 검색/)
  if (await search.count()) await search.fill('포스터')
  const poster = dialog.locator('div.group', { hasText: '포스터' }).first()
  await expect(poster).toBeVisible({ timeout: 20_000 })
  await poster.getByRole('button', { name: '이 서식으로 시작' }).click()

  // Both sentences, in the order they were written: theirs first, because it
  // is the one they were still working on.
  // `startsWith` rather than a regex built from the string: escaping one
  // metacharacter and not the rest is the kind of half-measure that passes
  // until somebody puts a `(` in a fixture.
  await expect
    .poll(async () => ((await composer.inputValue()) ?? '').startsWith(mine))
    .toBe(true)
  await expect(composer).toHaveValue(/포스터 그림/)

  // And the arriving half is selected, so the one keystroke that takes it back
  // out takes out exactly what the gallery put in.
  const selected = await composer.evaluate((el) => {
    const box = el as HTMLTextAreaElement
    return box.value.slice(box.selectionStart, box.selectionEnd)
  })
  expect(selected).toContain('포스터 그림')
  expect(selected).not.toContain('골목 어귀')
})

/**
 * The other surfaces, where nothing is written into the box at all.
 *
 * A 시작점 on a document surface rides with the turn and leaves the composer
 * empty — appending must not have quietly given it a sentence to type, which
 * is the failure the fix above could most easily introduce.
 */
test('표면을 옮겨도 챗 초안은 그대로 있고, 문서 입력창은 비어 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/chat')

  const composer = page.getByLabel('프롬프트 입력')
  const mine = '3월 정기 점검 결과를 정리해 줘.'
  await composer.fill(mine)

  // The home rail this used to press is gone — 서식 moved inside 작업 시작하기,
  // and the gallery shows only the surface it is open on. The way somebody
  // moves between surfaces now is the chip row, and the promise is the same
  // one: an unfinished sentence belongs to the surface it was typed on.
  // The chip switches the surface in place — one start screen, many surfaces —
  // so the check is on the composer rather than on the address.
  await page.getByRole('button', { name: '보고서', exact: true }).first().click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')

  // And back. A draft belongs to the surface it was typed on; moving away and
  // returning must not have spent it.
  await page.getByRole('button', { name: '챗', exact: true }).first().click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(mine)
})
