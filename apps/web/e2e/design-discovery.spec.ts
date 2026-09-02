import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.setTimeout(480_000)

test('슬라이드 생성 전에 실제 디자인과 사용 방식을 보고 고른다', async ({ page }) => {
  await signIn(page)
  await page.goto('/new/slides')
  await page.getByLabel('프롬프트 입력').fill('신입 연구원을 위한 연구윤리 교육 발표자료 7장을 만들어 줘.')
  await page.getByLabel('프롬프트 입력').press('Enter')

  const approve = page.getByRole('button', { name: '이대로 생성' })
  await expect(approve).toBeVisible({ timeout: 300_000 })
  await expect(page.getByText('어떤 인상으로 만들까요?')).toBeVisible()
  await expect(page.getByRole('button', { name: /정돈된 편집/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /강한 인상/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /차분한 여백/ })).toBeVisible()

  await page.getByRole('button', { name: /강한 인상/ }).click()
  await page.getByRole('button', { name: /자료만 전달/ }).click()
  await expect(page.getByRole('button', { name: /강한 인상/ })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: /자료만 전달/ })).toHaveAttribute('aria-pressed', 'true')

  const sent = page.waitForRequest((request) => {
    const body = request.postData() ?? ''
    return request.method() === 'POST' && body.includes('"visualStyle":"poster"') && body.includes('"density":"reading"')
  })
  await page.getByRole('button', { name: '고친 대로 생성' }).click()
  await sent
  await page.getByLabel('중지').click().catch(() => undefined)
})

test('320px 폭에서 상단 기능이 잘리거나 눌리지 않는다', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await signIn(page)
  await page.goto('/new/chat')
  await page.getByLabel('프롬프트 입력').fill('안녕하세요')
  await page.getByLabel('프롬프트 입력').press('Enter')

  const topbar = page.locator('header').first()
  const share = topbar.getByRole('button', { name: '공유' })
  await expect(share).toBeVisible({ timeout: 30_000 })
  for (const button of await topbar.locator('button').all()) {
    const box = await button.boundingBox()
    expect(box?.width).toBeGreaterThanOrEqual(32)
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(320)
  }
  expect(await topbar.evaluate((node) => node.scrollWidth)).toBeLessThanOrEqual(320)
  await page.getByLabel('중지').click().catch(() => undefined)
})
