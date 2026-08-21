import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

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

  await page.goto('/new/image')
  const composer = page.getByLabel('프롬프트 입력')
  const mine = '골목 어귀에서 손을 흔드는 아이. 늦은 오후 빛으로.'
  await composer.fill(mine)

  await page.getByRole('button', { name: '서식 고르기' }).click()
  const poster = page.getByRole('dialog').locator('div.group', { hasText: '포스터' })
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
test('문서 시작점은 여전히 입력창에 아무것도 쓰지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/report')

  const composer = page.getByLabel('프롬프트 입력')
  const mine = '3월 정기 점검 결과를 정리해 줘.'
  await composer.fill(mine)

  await page.getByRole('button', { name: '시작점 고르기' }).click()
  const card = page.getByRole('dialog').locator('div.group', { hasText: '장애 보고' })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.getByRole('button').first().click()

  await expect(page.getByRole('button', { name: '장애 보고 시작점 해제' })).toBeVisible()
  await expect(composer).toHaveValue(mine)
})
