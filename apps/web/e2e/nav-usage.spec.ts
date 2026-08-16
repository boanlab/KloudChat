import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('크레딧을 누르면 본인 사용량 화면으로 간다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/')
  await page.getByRole('button', { name: '이번 달 사용량' }).click()
  await expect(page).toHaveURL(/\/usage$/)
  await expect(page.getByRole('heading', { name: '사용량' })).toBeVisible({ timeout: 20_000 })
  // Exact: the sidebar's own credit readout also begins "이번 달", and the
  // loose match resolves to both the moment this page renders its card.
  await expect(page.getByText('이번 달', { exact: true })).toBeVisible()
  // Its own figures, never the instance's — the admin view is a different screen.
  await expect(page.getByText(/최근 \d+일 동안/)).toBeVisible({ timeout: 20_000 })
})

test('관리 항목은 사이드바에서 빠지고 계정 메뉴에 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/')
  // The navigation must not carry a second copy of the admin entries.
  await expect(page.getByRole('link', { name: '사용자 · 승인' })).toHaveCount(0)
  // Settings must not hide an instance-configuration tab behind a role check.
  await page.goto('/settings')
  await expect(page.getByRole('link', { name: '시스템' })).toHaveCount(0)

  await page.goto('/')
  await page.getByRole('button', { name: /e2e-personas@example\.com/ }).click()
  await page.getByRole('menuitem', { name: '시스템' }).click()
  await expect(page).toHaveURL(/\/admin\/system$/)
  await expect(page.getByText('메일 발송', { exact: true })).toBeVisible({ timeout: 20_000 })
})
