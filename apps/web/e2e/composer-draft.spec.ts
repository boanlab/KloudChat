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
  // 챗에서 확인한다. 규칙은 표면의 종류로 갈린다 — 그림과 영상에서는 문장이
  // 곧 프롬프트라 입력창에 들어가고, 나머지에서는 그 틀이 기계의 것이라 턴에
  // 실린다. 챗도 보고서도 '나머지' 쪽이고, 보고서의 기본 시작점은 같은 일을
  // 하는 서식이 생기면서 걷어냈으므로 카드가 남아 있는 쪽에서 본다.
  await page.goto('/new/chat')

  const composer = page.getByLabel('프롬프트 입력')
  const mine = '3월 정기 점검 결과를 정리해 줘.'
  await composer.fill(mine)

  await page.getByRole('button', { name: '서식 고르기' }).click()
  // A sentence card is one button, not a panel with a button inside it.
  const card = page.getByRole('dialog').getByRole('button').filter({ hasText: '장애 원인 좁히기' })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  await expect(page.getByRole('button', { name: '장애 원인 좁히기 시작점 해제' })).toBeVisible()
  await expect(composer).toHaveValue(mine)
})
