import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/**
 * 아무도 건드리지 않은 구성안은 「고침」이 아니다.
 *
 * The card holds the impression and the density in `useState`, seeded from
 * `pending.plan` — and `useState` keeps only the value of the first render.
 * The card mounts while the turn is still streaming, so on that render the
 * plan is usually not there yet: the seed froze at the default, the plan
 * arrived a moment later saying something else, and two things went wrong.
 *
 * The impression the outline had chosen for the subject was replaced by the
 * default without anybody asking — which undoes the whole point of choosing
 * one from the subject — and the card decided it had been edited, so its
 * button read 「고친 대로 생성」 on a card nobody had touched. A suite waiting
 * for 「이대로 생성」 then waits out its timeout beside a finished proposal.
 */

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

  // 카드가 설 때까지. The proposal arrives on a stream that is still going.
  const asIs = page.getByRole('button', { name: '이대로 생성' })
  const edited = page.getByRole('button', { name: '고친 대로 생성' })
  await expect(asIs.or(edited).first()).toBeVisible({ timeout: 300_000 })
  await page.waitForTimeout(2_000)

  await expect(edited, '건드리지 않았는데 고쳤다고 합니다').toHaveCount(0)
  await expect(asIs).toBeVisible()
  // 되돌릴 것이 없으므로 되돌리기도 없다.
  await expect(page.getByRole('button', { name: '처음 제안으로' })).toHaveCount(0)

  // 인상을 하나 고르면 그때는 고친 것이다.
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
