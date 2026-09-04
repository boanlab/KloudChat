import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/** An untouched proposal card offers 「이대로 생성」, keeping the plan's own impression;
 *  editing it switches to 「고친 대로 생성」. */

const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }

test('구성안을 건드리지 않으면 그대로 생성이라고 말한다', async ({ page }) => {
  test.setTimeout(420_000)
  await signInAs(page, USER.email, USER.password)
  await page.goto('/new/report')

  const box = page.getByLabel('프롬프트 입력')
  await expect(box).toBeVisible({ timeout: 20_000 })
  await box.fill(
    '학술지 투고용 원고의 「실험」 절을 써 주세요. 데이터, 설정, 지표, 결과 순서로 담아 주세요.',
  )
  await page.keyboard.press('Enter')

  // The proposal arrives on a stream that is still going.
  const asIs = page.getByRole('button', { name: '이대로 생성' })
  const edited = page.getByRole('button', { name: '고친 대로 생성' })
  await expect(asIs.or(edited).first()).toBeVisible({ timeout: 300_000 })
  await page.waitForTimeout(2_000)

  await expect(edited, '건드리지 않았는데 고쳤다고 합니다').toHaveCount(0)
  await expect(asIs).toBeVisible()
  // Nothing to revert, so no revert button.
  await expect(page.getByRole('button', { name: '처음 제안으로' })).toHaveCount(0)

  // Picking an impression counts as an edit.
  const another = page
    .getByRole('button', { name: /강한 인상|차분한 여백/ })
    .first()
  if (await another.isVisible().catch(() => false)) {
    await another.click()
    await page.waitForTimeout(600)
    await expect(edited, '고쳤는데 그대로라고 합니다').toBeVisible()
    await expect(page.getByRole('button', { name: '처음 제안으로' })).toBeVisible()
  }
})
