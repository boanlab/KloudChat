import { expect, test } from '@playwright/test'
import { openSidebar, signIn } from './helpers'

test('크레딧을 누르면 본인 사용량 화면으로 간다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/')
  await openSidebar(page)
  await page.getByRole('button', { name: '이번 달 사용량' }).click()
  await expect(page).toHaveURL(/\/usage$/)
  await expect(page.getByRole('heading', { name: '사용량' })).toBeVisible({ timeout: 20_000 })
  // Exact: the sidebar's credit readout also begins "이번 달".
  await expect(page.getByText('이번 달', { exact: true })).toBeVisible()
  // The account's own figures, not the instance's.
  await expect(page.getByText(/최근 \d+일 동안/)).toBeVisible({ timeout: 20_000 })
})

test('관리 항목은 사이드바에서 빠지고 계정 메뉴에 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/')
  await openSidebar(page)
  // No second copy of the admin entries in the navigation.
  await expect(page.getByRole('link', { name: '사용자 · 승인' })).toHaveCount(0)
  // No instance-configuration tab under settings.
  await page.goto('/settings')
  await expect(page.getByRole('link', { name: '시스템' })).toHaveCount(0)

  await page.goto('/')
  await openSidebar(page)
  await page.getByRole('button', { name: /e2e-personas@example\.com/ }).click()
  await page.getByRole('menuitem', { name: '시스템' }).click()
  await expect(page).toHaveURL(/\/admin\/system$/)
  // 시스템 is routed tabs; every part is reachable from the account menu.
  await page.getByRole('tab', { name: '메일' }).click()
  await expect(page).toHaveURL(/\/admin\/system\/mail$/)
  await expect(page.getByText('메일 발송', { exact: true })).toBeVisible({ timeout: 20_000 })
})
