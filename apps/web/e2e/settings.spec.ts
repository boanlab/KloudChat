/**
 * Settings tabs and the admin system screen — which is now tabs too.
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

test('기본 모델도 단가와 데이터 경계를 보고 고른다', async ({ page }) => {
  await page.goto('/settings/preferences')
  const chat = page.getByRole('button', { name: /^챗: / })
  await expect(chat).toBeVisible({ timeout: 20_000 })
  const before = (await chat.getAttribute('aria-label')) ?? ''

  await chat.click()
  const menu = page.getByRole('menu')
  // The composer's menu, so what governance asks about — the credit rate and
  // where the text goes — is on screen for the default too.
  await expect(menu.getByText(/크레딧|1k당/).first()).toBeVisible()
  // Auto is a property of one conversation. There is none here, so it is not
  // offered rather than offered and inert.
  await expect(menu.getByText('Auto · 비용 절약')).toHaveCount(0)

  // 메뉴에는 모델 행이 아닌 버튼(검색 지우기 등)도 있다 — 공급자 표기
  // '·' 를 가진 행만이 클릭하면 기본값이 바뀌는 행이다.
  const others = menu
    .locator('button')
    .filter({ hasText: '·' })
    .filter({ hasNotText: before.replace(/^챗: /, '') })
  test.skip((await others.count()) === 0, '이 인스턴스에는 챗 모델이 하나뿐입니다')
  await others.first().click()
  await expect(chat).not.toHaveAttribute('aria-label', before)

  // Same store action as the old select, so the choice still survives a reload.
  const after = (await chat.getAttribute('aria-label')) ?? ''
  await page.reload()
  await expect(page.getByRole('button', { name: /^챗: / })).toHaveAttribute('aria-label', after, {
    timeout: 20_000,
  })
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

test('관리자 시스템이 탭으로 나뉘고 각 탭이 URL을 가진다', async ({ page }) => {
  await page.goto('/admin/system')
  await expect(page.getByRole('tab', { name: '프록시' })).toBeVisible()
  await expect(page.getByLabel(/LiteLLM 주소/)).toBeVisible()

  await page.getByRole('tab', { name: '라우팅' }).click()
  await expect(page).toHaveURL(/\/admin\/system\/routing$/)
  await expect(page.getByRole('heading', { name: '모델 자동 라우팅' })).toBeVisible()
  // The point of the split: the proxy is no longer part of this scroll.
  await expect(page.getByLabel(/LiteLLM 주소/)).toHaveCount(0)

  await page.getByRole('tab', { name: '기능' }).click()
  await expect(page).toHaveURL(/\/admin\/system\/features$/)
  await expect(page.getByRole('heading', { name: '사용할 기능' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '기능 연동' })).toBeVisible()

  await page.getByRole('tab', { name: '공용 템플릿' }).click()
  await expect(page).toHaveURL(/\/admin\/system\/templates$/)
  await expect(page.getByRole('region', { name: '공용 템플릿' })).toBeVisible()

  await page.getByRole('tab', { name: '브랜딩' }).click()
  await expect(page).toHaveURL(/\/admin\/system\/branding$/)
  await expect(page.getByRole('heading', { name: '브랜딩' })).toBeVisible()

  await page.getByRole('tab', { name: '메일' }).click()
  await expect(page).toHaveURL(/\/admin\/system\/mail$/)
  // One tab, one save unit: this is the only 저장 button on it.
  await expect(page.getByRole('button', { name: '메일 설정 저장' })).toBeVisible()

  // Deep-linkable: a reload lands on the same tab, so the URL is something you
  // can send to whoever has to fill the relay in.
  await page.reload()
  await expect(page).toHaveURL(/\/admin\/system\/mail$/)
  await expect(page.getByRole('button', { name: '메일 설정 저장' })).toBeVisible({
    timeout: 20_000,
  })

  // A path no tab claims falls back to the first one rather than to an empty
  // page.
  await page.goto('/admin/system/nowhere')
  await expect(page).toHaveURL(/\/admin\/system$/)
  await expect(page.getByLabel(/LiteLLM 주소/)).toBeVisible({ timeout: 20_000 })
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

test('모델 목록을 새로고침하면 몇 종을 받아 왔는지 알려 준다', async ({ page }) => {
  await page.goto('/admin/system')
  await expect(page.getByText(/LiteLLM 에 연결(되어 있습니다|되지 않았습니다)/)).toBeVisible({
    timeout: 20_000,
  })

  // The point of the button is that it goes to the server rather than reusing
  // what the screen already has, so watch for the request as well as the text.
  const [refreshed] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/api/models/refresh') && r.request().method() === 'POST',
    ),
    page.getByRole('button', { name: '모델 목록 새로고침' }).click(),
  ])
  expect(refreshed.ok()).toBe(true)

  await expect(page.getByText(/모델 목록을 다시 읽었습니다 · 모델 [0-9]+종/)).toBeVisible({
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
    // Anchored on the field rather than on the page: the tab it belongs to is
    // the save unit, and this says so out loud.
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
