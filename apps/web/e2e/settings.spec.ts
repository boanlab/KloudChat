/**
 * Settings tabs and the admin proxy screen.
 *
 * Run with: npx playwright test e2e/settings.spec.ts --project=desktop
 */

import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

test('설정이 탭으로 나뉘고 각 탭이 URL을 가진다', async ({ page }) => {
  await page.goto('/settings')
  await expect(page.getByRole('tab', { name: '프로필' })).toBeVisible()
  await expect(page.getByLabel('이름')).toBeVisible()

  await page.getByRole('tab', { name: '환경설정' }).click()
  await expect(page).toHaveURL(/\/settings\/preferences$/)
  await expect(page.getByText('기본 모델')).toBeVisible()

  await page.getByRole('tab', { name: 'API 키' }).click()
  await expect(page).toHaveURL(/\/settings\/keys$/)

  // Deep-linkable: a reload lands on the same tab.
  await page.reload()
  await expect(page).toHaveURL(/\/settings\/keys$/)
  await expect(page.getByRole('tab', { name: 'API 키' })).toBeVisible({ timeout: 20_000 })

  // Instance configuration is not here any more. Every tab on this screen is
  // about the person looking at it; the proxy and the mail relay are about the
  // deployment, and they moved to the admin screens (see nav-usage.spec.ts).
  await expect(page.getByRole('tab', { name: '시스템' })).toHaveCount(0)
})

test('이름을 바꾸면 저장되고 새로고침 후에도 남는다', async ({ page }) => {
  const name = `이름 ${Math.random().toString(36).slice(2, 7)}`
  await page.goto('/settings')
  await page.getByLabel('이름').fill(name)
  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect(page.getByText('저장됨')).toBeVisible({ timeout: 15_000 })

  await page.reload()
  await expect(page.getByLabel('이름')).toHaveValue(name, { timeout: 20_000 })
})

test('시스템 화면이 연결 상태를 보여 주고 마스터 키는 노출하지 않는다', async ({ page }) => {
  await page.goto('/admin/system')
  // The settled state, not the loading one: /LiteLLM 에 연결/ also matches the
  // "checking connection status" string, which would pass before any data
  // lands.
  await expect(page.getByText(/LiteLLM 에 연결(되어 있습니다|되지 않았습니다)/)).toBeVisible({
    timeout: 20_000,
  })

  // The key field starts empty even though a key is configured.
  await expect(page.getByLabel(/마스터 키/)).toHaveValue('')

  await page.getByRole('button', { name: '연결 테스트', exact: true }).click()
  await expect(page.getByText(/연결됨 · 모델 [0-9]+종|연결하지 못했습니다/)).toBeVisible({
    timeout: 30_000,
  })
})

test('잘못된 주소를 저장하면 연결이 끊기고, 되돌리면 복구된다', async ({ page }) => {
  await page.goto('/admin/system')
  // Wait for the form to hold the real value before typing over it. Filling
  // while the fetch is still in flight raced the state update and left the two
  // addresses concatenated in the box.
  await expect(page.getByLabel(/LiteLLM 주소/)).not.toHaveValue('', { timeout: 20_000 })

  try {
    await page.getByLabel(/LiteLLM 주소/).fill('http://nowhere.invalid:9999')
    await expect(page.getByLabel(/LiteLLM 주소/)).toHaveValue('http://nowhere.invalid:9999')
    // This screen has a save button per section. Press only the one in the
    // same group as the address field.
    await page
      .locator('label', { hasText: 'LiteLLM 주소' })
      .locator('xpath=ancestor::*[.//button][1]')
      .getByRole('button', { name: '저장', exact: true })
      .first()
      .click()
    await expect(page.getByText('LiteLLM 에 연결되지 않았습니다')).toBeVisible({ timeout: 30_000 })
  } finally {
    // This test writes *instance-wide* configuration. Leaving a bad proxy behind
    // does not fail one test, it fails every test after it — and the running
    // instance with it. The revert runs even when the assertion does not.
    //
    // Through the UI rather than the API: the access token lives in page memory,
    // so `page.request` would go out unauthenticated.
    await page
      .getByRole('button', { name: '환경변수로 되돌리기' })
      .click({ timeout: 15_000 })
      .catch(() => {})
  }

  await expect(page.getByText('LiteLLM 에 연결되어 있습니다')).toBeVisible({ timeout: 30_000 })
})
