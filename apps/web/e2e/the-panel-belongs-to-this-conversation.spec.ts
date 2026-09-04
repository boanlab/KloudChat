import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

/** The artifact panel belongs to the current conversation: start screens and other
 *  conversations do not show the previous one's document. */

const ADMIN = { email: 'admin@kloud.zone', password: 'KloudChat-Admin-2026' }

test('덱을 열어 둔 채 새 작업으로 가면 패널이 비워진다', async ({ page }) => {
  test.setTimeout(240_000)
  await signInAs(page, ADMIN.email, ADMIN.password)

  // A conversation with a document open.
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.waitForTimeout(1_500)
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel).toBeVisible({ timeout: 30_000 })
  const deckTitle = (await panel.innerText()).split('\n')[0]

  // Then to each start screen.
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

  // A conversation with a document, then one without.
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.waitForTimeout(1_500)
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel).toBeVisible({ timeout: 30_000 })
  const deckTitle = (await panel.innerText()).split('\n')[0]

  // A plain chat from 기록.
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
