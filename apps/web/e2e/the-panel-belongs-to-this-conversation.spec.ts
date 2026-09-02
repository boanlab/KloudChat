import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/**
 * 시작 화면에는 앞 대화의 문서가 따라오지 않는다.
 *
 * `openArtifactId` is global, and nothing on the way to the start screen
 * cleared it. So leaving a deck and pressing 새로 만들기 opened an empty
 * composer with the previous conversation's deck still open beside it — and it
 * stayed through the next turn, so somebody watching their 보고서 being written
 * spent that minute looking at slides from a conversation they had left.
 *
 * Reported as 「보고서 쪽에도 갑자기 슬라이드가?」, which is exactly what it
 * looked like.
 */

const ADMIN = { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' }

test('덱을 열어 둔 채 새 작업으로 가면 패널이 비워진다', async ({ page }) => {
  test.setTimeout(240_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  // 문서가 열린 대화 하나.
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.waitForTimeout(1_500)
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel).toBeVisible({ timeout: 30_000 })
  const deckTitle = (await panel.innerText()).split('\n')[0]

  // 그 상태에서 새 작업 화면으로.
  for (const route of ['/new/report', '/new/chat', '/new/slides', '/']) {
    await page.goto(route)
    await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 20_000 })
    await page.waitForTimeout(700)
    await expect(panel, `${route} 에 앞 대화의 문서가 남았습니다`).toBeHidden()
    const body = await page.locator('main').innerText()
    expect(body, `${route} 에 앞 덱의 제목이 남았습니다`).not.toContain(deckTitle)
  }
})

test('대화를 옮기면 그 대화의 문서만 보인다', async ({ page }) => {
  test.setTimeout(240_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  // 문서가 있는 대화와, 없는 대화를 차례로 연다.
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.waitForTimeout(1_500)
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel).toBeVisible({ timeout: 30_000 })
  const deckTitle = (await panel.innerText()).split('\n')[0]

  // 문서가 없는 평범한 대화 — 기록에서 챗 하나.
  await page.goto('/history')
  const plain = page.getByRole('button', { name: /인사|날짜|계산|화학식/ }).first()
  if (await plain.isVisible().catch(() => false)) {
    await plain.click()
    await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
    await page.waitForTimeout(1_200)
    const body = await page.locator('main').innerText()
    expect(body, '문서 없는 대화에 앞 덱이 남았습니다').not.toContain(deckTitle)
  }
})
