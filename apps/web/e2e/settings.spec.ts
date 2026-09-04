/** Settings tabs and the admin system tabs. */

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

  // Deep-linkable.
  await page.reload()
  await expect(page).toHaveURL(/\/settings\/keys$/)
  await expect(page.getByRole('tab', { name: 'API 키' })).toBeVisible({ timeout: 20_000 })

  // Instance configuration lives on the admin screens.
  await expect(page.getByRole('tab', { name: '시스템' })).toHaveCount(0)
})

test('기본 모델도 단가와 데이터 경계를 보고 고른다', async ({ page }) => {
  await page.goto('/settings/preferences')
  const chat = page.getByRole('button', { name: /^챗: / })
  await expect(chat).toBeVisible({ timeout: 20_000 })
  const before = (await chat.getAttribute('aria-label')) ?? ''

  await chat.click()
  const menu = page.getByRole('menu')
  // The composer's menu: credit rate and data boundary are shown.
  await expect(menu.getByText(/크레딧|1k당/).first()).toBeVisible()
  // Auto belongs to a conversation; none here.
  await expect(menu.getByText('Auto · 비용 절약')).toHaveCount(0)

  // Only rows with the '·' vendor separator are model rows.
  const others = menu
    .locator('button')
    .filter({ hasText: '·' })
    .filter({ hasNotText: before.replace(/^챗: /, '') })
  test.skip((await others.count()) === 0, '이 인스턴스에는 챗 모델이 하나뿐입니다')
  await others.first().click()
  await expect(chat).not.toHaveAttribute('aria-label', before)

  // Survives a reload.
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
  // The proxy is not on this tab.
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
  // One tab, one save unit.
  await expect(page.getByRole('button', { name: '메일 설정 저장' })).toBeVisible()

  // Deep-linkable.
  await page.reload()
  await expect(page).toHaveURL(/\/admin\/system\/mail$/)
  await expect(page.getByRole('button', { name: '메일 설정 저장' })).toBeVisible({
    timeout: 20_000,
  })

  // An unclaimed path falls back to the first tab.
  await page.goto('/admin/system/nowhere')
  await expect(page).toHaveURL(/\/admin\/system$/)
  await expect(page.getByLabel(/LiteLLM 주소/)).toBeVisible({ timeout: 20_000 })
})

test('시스템 화면이 연결 상태를 보여 주고 마스터 키는 노출하지 않는다', async ({ page }) => {
  await page.goto('/admin/system')
  // The settled state: /LiteLLM 에 연결/ alone also matches the checking string.
  await expect(page.getByText(/LiteLLM 에 연결(되어 있습니다|되지 않았습니다)/)).toBeVisible({
    timeout: 20_000,
  })

  // The key field starts empty even with a key configured.
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

  // Watch for the request as well as the text.
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
  // Wait for the form to hold the real value before typing over it.
  await expect(page.getByLabel(/LiteLLM 주소/)).not.toHaveValue('', { timeout: 20_000 })

  try {
    await page.getByLabel(/LiteLLM 주소/).fill('http://nowhere.invalid:9999')
    await expect(page.getByLabel(/LiteLLM 주소/)).toHaveValue('http://nowhere.invalid:9999')
    // The tab's own 저장.
    await page
      .locator('label', { hasText: 'LiteLLM 주소' })
      .locator('xpath=ancestor::*[.//button][1]')
      .getByRole('button', { name: '저장', exact: true })
      .first()
      .click()
    await expect(page.getByText('LiteLLM 에 연결되지 않았습니다')).toBeVisible({ timeout: 30_000 })
  } finally {
    // Instance-wide configuration: always revert. Through the UI, since `page.request` has no token.
    await page
      .getByRole('button', { name: '환경변수로 되돌리기' })
      .click({ timeout: 15_000 })
      .catch(() => {})
  }

  await expect(page.getByText('LiteLLM 에 연결되어 있습니다')).toBeVisible({ timeout: 30_000 })
})
