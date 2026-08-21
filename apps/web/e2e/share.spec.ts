import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * A share link opens for someone with no account.
 *
 * Reading it while signed out is the assertion that matters: a link that only
 * ever opens for the person who created it looks perfectly fine.
 */
test('링크를 만들면 로그인하지 않은 사람도 열 수 있다', async ({ page, browser }) => {
  test.setTimeout(180_000)
  await signIn(page)

  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('공유 확인용입니다. 한 문장으로 답해줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 120_000 })

  await page.getByRole('button', { name: '공유', exact: true }).click()
  const modal = page.getByRole('dialog')
  await modal.getByText('링크가 있는 사람').click()
  await modal.getByRole('button', { name: '링크 만들기' }).click()

  const field = modal.getByLabel('공유 링크')
  await expect(field).toBeVisible({ timeout: 20_000 })
  const url = await field.inputValue()
  expect(url).toMatch(/\/share\/[\w-]{20,}$/)

  // Said in the top bar, on a page nobody has asked a question of. A shared
  // conversation that looks private is how one gets left open.
  await modal.getByRole('button', { name: '완료' }).click()
  await expect(page.getByText('링크 공개 중')).toBeVisible()
  await page.reload()
  await expect(page.getByText('링크 공개 중')).toBeVisible({ timeout: 20_000 })

  // A brand-new context: no cookie, no token, nothing this instance has ever
  // seen. This is the customer the sales persona hands the link to.
  const stranger = await browser.newContext()
  const guest = await stranger.newPage()
  await guest.goto(url)
  await expect(guest.getByText('읽기 전용')).toBeVisible({ timeout: 20_000 })
  // `.first()`: the phrase is in the question and, depending on how the model
  // opens its answer, in the answer too — two matches fail strict mode for a
  // reason that has nothing to do with sharing.
  await expect(guest.getByText('공유 확인용입니다', { exact: false }).first()).toBeVisible()
  // Nothing to walk to: the page is outside the shell.
  await expect(guest.getByRole('link', { name: '아티팩트' })).toHaveCount(0)

  // Revoked links stop working, and say nothing about having existed.
  await page.getByRole('button', { name: '공유', exact: true }).click()
  await page.getByRole('button', { name: '링크 철회' }).click()
  await expect(page.getByRole('button', { name: '링크 만들기' })).toBeVisible({ timeout: 20_000 })
  await modal.getByRole('button', { name: '완료' }).click()
  await expect(page.getByText('링크 공개 중')).toHaveCount(0)
  await guest.goto(url)
  await expect(guest.getByText('열 수 없는 링크입니다')).toBeVisible({ timeout: 20_000 })
  await stranger.close()
})
