import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('공유 링크를 누가 열었는지 남는다', async ({ page, context }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('공유 열람 기록 확인용 한 줄')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  await page.getByRole('button', { name: '공유', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('계정이 있는 사람')).toBeVisible()
  await dialog.getByRole('button', { name: '링크가 있는 사람' }).click()
  await dialog.getByRole('button', { name: '링크 만들기' }).click()
  const url = await dialog.getByLabel('공유 링크').inputValue()
  expect(url).toContain('/share/')
  await expect(dialog.getByText('아직 아무도 열지 않았습니다.')).toBeVisible({ timeout: 20_000 })

  // A reader with no account here — the case `link` scope exists for.
  const anon = await context.browser()!.newContext()
  const guest = await anon.newPage()
  await guest.goto(url)
  await guest.waitForTimeout(1500)
  await anon.close()

  await dialog.getByRole('button', { name: '완료' }).click()
  await page.getByRole('button', { name: '공유', exact: true }).click()
  const list = page.getByRole('dialog').getByRole('list').last()
  await expect(list.getByText('계정 없는 방문자')).toBeVisible({ timeout: 20_000 })
  const row = (await list.locator('li').first().textContent())?.replace(/\s+/g, ' ').trim()
  console.log('열람 기록:', row)
  expect(row).toMatch(/Chrome|Firefox|Safari|Edge/)
})
