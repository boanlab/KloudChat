import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/** 선택 삭제 after 전체 선택 confirms first, says how many, and cancelling leaves the list untouched. */

const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }

test('전체 선택 다음의 삭제는 몇 개인지 말하고 물어본다', async ({ page }) => {
  test.setTimeout(180_000)
  await signInAs(page, USER.email, USER.password)
  await page.goto('/history')
  await expect(page.getByLabel('대화 검색')).toBeVisible({ timeout: 20_000 })

  const before = await page.locator('main input[type="checkbox"]').count()
  test.skip(before < 2, '대화가 적어 확인할 것이 없습니다')

  await page.getByRole('button', { name: '보이는 항목 전체 선택' }).click()
  await page.waitForTimeout(500)

  // The count is on the button.
  const remove = page.getByRole('button', { name: /선택 \d+개 삭제/ })
  await expect(remove).toBeVisible()
  const label = (await remove.innerText()).trim()
  const staged = Number(label.match(/\d+/)?.[0] ?? 0)
  expect(staged, '고른 개수가 버튼에 적히지 않습니다').toBeGreaterThan(1)

  // Asks instead of deleting.
  await remove.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog, '확인 없이 바로 지웁니다').toBeVisible({ timeout: 10_000 })
  await expect(dialog, '몇 개를 지우는지 확인 문구가 말하지 않습니다').toContainText(
    String(staged),
  )

  // Cancelling changes nothing.
  const cancel = dialog.getByRole('button', { name: /취소|닫기/ }).first()
  await expect(cancel, '물러날 버튼이 없습니다').toBeVisible()
  await cancel.click()
  await expect(dialog).toBeHidden({ timeout: 10_000 })

  await page.reload()
  await expect(page.getByLabel('대화 검색')).toBeVisible({ timeout: 20_000 })
  const after = await page.locator('main input[type="checkbox"]').count()
  expect(after, '취소했는데 대화가 사라졌습니다').toBe(before)
})
