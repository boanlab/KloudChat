import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/**
 * 작업 시작하기는 한 가지만 묻는다.
 *
 * It used to be two tabs: 업무 시작점 for what you are doing, 결과 서식 for
 * what it looks like. The second is a question about typography asked of
 * somebody who came to write an incident report — which has a shape, and that
 * shape is `doc-incident`. Two decisions for one job, and the one people
 * skipped was the one that decided how the result read.
 *
 * So a 시작점 carries the 서식 its job comes in, and says so on the card. A job
 * with no fixed shape carries none, and then the writing surface chooses the
 * colour and the impression from the subject instead.
 */

const USER = { email: 'test@kloud.zone', password: 'KloudChat-Test-2026' }

test('두 갈래로 묻지 않는다', async ({ page }) => {
  test.setTimeout(180_000)
  await signInAs(page, USER.email, USER.password)

  for (const kind of ['report', 'slides', 'chat']) {
    await page.goto(`/new/${kind}`)
    await page.getByRole('button', { name: '작업 시작하기' }).first().click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible({ timeout: 20_000 })

    await expect(dialog.getByRole('tab', { name: /결과 서식/ }), `${kind}: 서식 탭이 남았습니다`)
      .toHaveCount(0)
    await expect(dialog.getByRole('tab', { name: /업무 시작점/ }), `${kind}: 시작점 탭이 남았습니다`)
      .toHaveCount(0)
    await expect(dialog.getByText('어떤 일을 시작할까요?')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden({ timeout: 10_000 })
  }
})

test('모양이 정해진 일은 그 모양을 카드에 적어 둔다', async ({ page }) => {
  test.setTimeout(180_000)
  await signInAs(page, USER.email, USER.password)
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })

  // 의사결정 보고서 is a 한 장 요약 — that is what the job is.
  await dialog.getByPlaceholder(/검색/).fill('의사결정 보고서')
  const decision = dialog.locator('.grid > *').filter({ hasText: '의사결정 보고서' }).first()
  await expect(decision).toBeVisible({ timeout: 20_000 })
  await expect(decision, '서식이 카드에 적혀 있지 않습니다').toContainText('한 장 요약')

  // 문헌 동향 조사 has no house style, and says nothing rather than saying a
  // default — the surface picks the look from the subject.
  await dialog.getByPlaceholder(/검색/).fill('문헌 동향 조사')
  const survey = dialog.locator('.grid > *').filter({ hasText: '문헌 동향 조사' }).first()
  await expect(survey).toBeVisible({ timeout: 20_000 })
  await expect(survey).not.toContainText('한 장 요약')
})

test('시작점을 고르면 그 서식까지 함께 걸친다', async ({ page }) => {
  test.setTimeout(180_000)
  await signInAs(page, USER.email, USER.password)
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  await dialog.getByPlaceholder(/검색/).fill('장애 사후 분석')
  await page.getByRole('button', { name: '장애 사후 분석 시작점 선택' }).click()
  await expect(dialog).toBeHidden({ timeout: 10_000 })

  // Both chips on the composer: the job, and the shape it comes in.
  await expect(page.getByRole('button', { name: '장애 사후 분석 시작점 해제' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByText('장애 보고', { exact: true }).first()).toBeVisible({
    timeout: 20_000,
  })
})
