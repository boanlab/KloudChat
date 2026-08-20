import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The empty screen of a surface that does not answer in words.
 *
 * A clip and a piece of audio are started from the composer, where their
 * length, resolution and voice are chosen. The example cards used to call
 * `send`, which appended the person's sentence and an assistant reply that had
 * never been generated — a turn that had not happened, and one that vanished
 * on the next reload because nothing stored it.
 *
 * Nothing here generates anything, so nothing here costs credits.
 */
test('오디오·영상 예시 카드는 보내지 않고 입력창에 넣는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/av')

  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toHaveValue('', { timeout: 20_000 })

  const example = page.getByRole('group', { name: '이렇게 시작해 보세요' }).getByRole('button').first()
  const text = ((await example.textContent()) ?? '').trim()
  await example.click()

  // The sentence lands where the length and the voice are, and it is still
  // editable — which is the whole reason it goes there rather than out.
  await expect(box).toHaveValue(text)
  // And no turn was invented: the screen is still the empty one.
  await expect(page.getByRole('group', { name: '이렇게 시작해 보세요' })).toBeVisible()
})

test('이미지 예시 카드도 같은 자리에 넣는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/image')

  const example = page.getByRole('group', { name: '이렇게 시작해 보세요' }).getByRole('button').first()
  const text = ((await example.textContent()) ?? '').trim()
  await example.click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(text)
})
