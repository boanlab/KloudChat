import { expect, test } from '@playwright/test'
import { signInAs } from './helpers'

const ordinaryEmail = process.env.KCHAT_ORDINARY_EMAIL
const ordinaryPassword = process.env.KCHAT_ORDINARY_PASSWORD

test.skip(
  !ordinaryEmail || !ordinaryPassword,
  'KCHAT_ORDINARY_EMAIL과 KCHAT_ORDINARY_PASSWORD가 필요합니다.',
)

test.beforeEach(async ({ page }) => {
  await signInAs(page, ordinaryEmail!, ordinaryPassword!)
})

test('일반 사용자는 업무 화면을 쓰지만 관리자 화면에는 들어갈 수 없다', async ({ page }) => {
  await page.goto('/new/chat')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await page.goto('/projects')
  await expect(page.getByRole('button', { name: '새 프로젝트' }).first()).toBeVisible()
  await page.goto('/artifacts')
  await expect(page.getByRole('heading', { name: '아티팩트' })).toBeVisible()

  for (const route of ['/admin/users', '/admin/usage', '/admin/system', '/admin/governance']) {
    const response = await page.goto(route)
    expect(response?.status(), `${route} HTTP 상태`).toBeLessThan(500)
    await expect(page.getByText(/접근 권한|권한이 없|접근할 수 없/).first()).toBeVisible()
  }
})
