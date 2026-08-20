import { expect, test } from '@playwright/test'
import { openSidebar, signIn } from './helpers'

test('크레딧을 누르면 본인 사용량 화면으로 간다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)
  await page.goto('/')
  // The credit readout lives in the sidebar, and below 1024px that sidebar is
  // a drawer which starts closed. Opening it is the tap the reader makes too.
  await openSidebar(page)
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
  // On a narrow layout the sidebar is a closed overlay, and a navigation nobody
  // can see proves nothing about what it does not carry.
  await openSidebar(page)
  // The navigation must not carry a second copy of the admin entries.
  await expect(page.getByRole('link', { name: '사용자 · 승인' })).toHaveCount(0)
  // Settings must not hide an instance-configuration tab behind a role check.
  await page.goto('/settings')
  await expect(page.getByRole('link', { name: '시스템' })).toHaveCount(0)

  await page.goto('/')
  await openSidebar(page)
  await page.getByRole('button', { name: /e2e-personas@example\.com/ }).click()
  await page.getByRole('menuitem', { name: '시스템' }).click()
  await expect(page).toHaveURL(/\/admin\/system$/)
  // 시스템 is six routed tabs now, one per operator job, so the relay is no
  // longer on the page this menu item lands on — it is one tab away. What the
  // test is here for is unchanged: instance configuration answers to the
  // account menu, and every part of it is reachable from there.
  await page.getByRole('tab', { name: '메일' }).click()
  await expect(page).toHaveURL(/\/admin\/system\/mail$/)
  await expect(page.getByText('메일 발송', { exact: true })).toBeVisible({ timeout: 20_000 })
})
