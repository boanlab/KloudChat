import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A required field says it is required, and pressing 저장 without it says
 * what is missing. The button used to be disabled at 45% opacity with no
 * word about why, which read as the save failing silently.
 */
test('이름 없이 저장하면 무엇이 빠졌는지 말한다', async ({ page }) => {
  await signIn(page)
  await page.goto('/agents')
  await page.getByRole('button', { name: '새 에이전트' }).first().click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('필수 항목입니다.')).toBeVisible()

  await dialog.getByRole('button', { name: '저장' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('이름을 입력하세요.')
  await expect(dialog.getByLabel('이름')).toBeFocused()
  // Nothing was sent, and the dialog is still here with the form in it.
  await expect(dialog).toBeVisible()

  await dialog.getByLabel('이름').fill('이름 있음')
  await expect(dialog.getByRole('alert')).toHaveCount(0)
  await dialog.getByRole('button', { name: '취소' }).click()
})
