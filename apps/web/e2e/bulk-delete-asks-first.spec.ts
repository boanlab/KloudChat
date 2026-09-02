import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/**
 * 한 번 눌러 전부 고르고, 그 옆이 삭제다.
 *
 * 대화 기록 puts 「보이는 항목 전체 선택」 immediately beside 「선택 N개 삭제」
 * and 「모든 대화 삭제」. One press stages every conversation the account has;
 * the next press is next to it. What stands between them is a confirmation, so
 * this checks that the confirmation is there, that it says how many, and that
 * closing it leaves the list untouched — the state a mis-click has to land in.
 */

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

  // 고른 개수가 버튼에 적힌다 — 무엇을 지우는지 누르기 전에 보인다.
  const remove = page.getByRole('button', { name: /선택 \d+개 삭제/ })
  await expect(remove).toBeVisible()
  const label = (await remove.innerText()).trim()
  const staged = Number(label.match(/\d+/)?.[0] ?? 0)
  expect(staged, '고른 개수가 버튼에 적히지 않습니다').toBeGreaterThan(1)

  // 누르면 지우지 않고 묻는다.
  await remove.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog, '확인 없이 바로 지웁니다').toBeVisible({ timeout: 10_000 })
  await expect(dialog, '몇 개를 지우는지 확인 문구가 말하지 않습니다').toContainText(
    String(staged),
  )

  // 물러날 길이 있고, 물러나면 아무 일도 없었어야 한다.
  const cancel = dialog.getByRole('button', { name: /취소|닫기/ }).first()
  await expect(cancel, '물러날 버튼이 없습니다').toBeVisible()
  await cancel.click()
  await expect(dialog).toBeHidden({ timeout: 10_000 })

  await page.reload()
  await expect(page.getByLabel('대화 검색')).toBeVisible({ timeout: 20_000 })
  const after = await page.locator('main input[type="checkbox"]').count()
  expect(after, '취소했는데 대화가 사라졌습니다').toBe(before)
})
