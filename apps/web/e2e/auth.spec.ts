/** Authentication against the real backend. The bootstrap test needs an empty `users` table and skips otherwise. */

import { expect, test } from '@playwright/test'
import { E2E_ADMIN, seedPendingUser } from './helpers'

/** The seeded, approved account (`scripts/e2e-seed.sh`) used by every test but bootstrap. */
const ADMIN = E2E_ADMIN

/** Bootstrap-only account; an address nothing else claims. */
const BOOTSTRAP = {
  email: 'e2e-bootstrap@example.com',
  password: 'correct-horse-battery',
  name: '관리자',
}
// Unique per run: the pending-approval path exists only for a never-approved account.
const USER = {
  email: `e2e-user-${Date.now().toString(36)}@example.com`,
  password: 'another-long-password',
  name: '학생',
}

// Signup order matters (first account becomes admin).
test.describe.configure({ mode: 'serial' })

/** Signed-in marker at every width; sidebar links are absent below 1024px. */
const shell = (page: import('@playwright/test').Page) =>
  page.getByRole('button', { name: '사이드바 토글' })

async function fillAuthForm(
  page: import('@playwright/test').Page,
  mode: 'login' | 'signup',
  who: { email: string; password: string; name?: string },
) {
  // Tab and submit share the label 로그인: tab from outside the form, submit inside it.
  const form = page.locator('form')
  await page
    .getByRole('button', { name: mode === 'login' ? '로그인' : '회원가입', exact: true })
    .first()
    .click()
  if (mode === 'signup') await page.getByLabel('이름').fill(who.name ?? '')
  await page.getByLabel('이메일').fill(who.email)
  await page.getByLabel('비밀번호').fill(who.password)

  // Wait for the response: navigating mid-POST drops the Set-Cookie.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/auth/${mode}`) && r.request().method() === 'POST',
    ),
    form.getByRole('button', { name: mode === 'login' ? '로그인' : '가입 요청' }).click(),
  ])
}

test('첫 가입 계정은 관리자로 바로 입장한다', async ({ page }) => {
  await page.goto('/')
  await fillAuthForm(page, 'signup', BOOTSTRAP)

  // 409 or `pending` both mean the database is not fresh.
  const pending = page.getByRole('heading', { name: '승인을 기다리는 중입니다' })
  const taken = page.getByText('이미 사용 중인 이메일입니다.')
  const notFresh = await Promise.race([
    pending.waitFor({ timeout: 6_000 }).then(() => true),
    taken.waitFor({ timeout: 6_000 }).then(() => true),
  ]).catch(() => false)
  if (notFresh) {
    test.skip(true, '빈 DB 가 아닙니다 — 부트스트랩은 첫 가입에서만 검증됩니다.')
  }

  await expect(page.getByRole('link', { name: '아티팩트' })).toBeVisible({ timeout: 15_000 })
})

test('모델 선택기가 LiteLLM 카탈로그를 보여 준다', async ({ page }) => {
  await page.goto('/')
  await fillAuthForm(page, 'login', ADMIN)
  await page.goto('/new/chat')
  // Shape of a live catalogue, not a named model: several entries with per-1k pricing.
  await page.getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i }).first().click()
  const priced = page.getByRole('menu').getByText(/1k당 입력|무료/)
  await expect(priced.first()).toBeVisible()
  expect(await priced.count(), '카탈로그 항목 수').toBeGreaterThan(3)
})

test('잘못된 비밀번호는 폼에 사유를 보여 준다', async ({ page }) => {
  await page.goto('/')
  await fillAuthForm(page, 'login', { ...ADMIN, password: 'definitely-not-it' })
  await expect(page.getByText('이메일 또는 비밀번호가 올바르지 않습니다.')).toBeVisible()
})

test('두 번째 가입은 승인 대기 화면에 머문다', async ({ page }) => {
  await page.goto('/')
  await fillAuthForm(page, 'signup', USER)
  await expect(page.getByRole('heading', { name: '승인을 기다리는 중입니다' })).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByText(USER.email)).toBeVisible()
})

test('관리자가 승인하면 대기 화면이 스스로 넘어간다', async ({ browser }) => {
  const waiting = await browser.newContext()
  const admin = await browser.newContext()
  const waitingPage = await waiting.newPage()
  const adminPage = await admin.newPage()

  // Seed the pending account so this test also runs on its own (409 if the previous test made it).
  await seedPendingUser(waitingPage, USER.email, USER.password)

  await waitingPage.goto('/')
  await fillAuthForm(waitingPage, 'login', USER)
  await expect(waitingPage.getByRole('heading', { name: '승인을 기다리는 중입니다' })).toBeVisible({
    timeout: 15_000,
  })

  await adminPage.goto('/')
  await fillAuthForm(adminPage, 'login', ADMIN)
  await adminPage.goto('/admin/users')
  await expect(
    adminPage.getByPlaceholder('이름 또는 이메일'),
    `${ADMIN.email} 에 관리자 권한이 없습니다 — scripts/e2e-seed.sh 를 실행하세요`,
  ).toBeVisible({ timeout: 15_000 })
  // The table pages at forty rows.
  await adminPage.getByPlaceholder('이름 또는 이메일').fill(USER.email)
  const row = adminPage.locator('tr', { hasText: USER.email })
  await expect(row).toBeVisible({ timeout: 15_000 })
  // Exact: the row also has a "<이름> 모델 제한" button.
  await row.getByRole('button', { name: '승인', exact: true }).click()
  await expect(row.getByText('활성')).toBeVisible({ timeout: 10_000 })

  // The pending screen polls every 15s; no reload, no re-login.
  await expect(shell(waitingPage)).toBeVisible({ timeout: 40_000 })

  await waiting.close()
  await admin.close()
})

test('새로고침해도 로그인이 유지된다', async ({ page }) => {
  await page.goto('/')
  await fillAuthForm(page, 'login', ADMIN)
  await expect(shell(page)).toBeVisible({ timeout: 15_000 })
  // The access token is memory-only; surviving a reload proves the refresh cookie round-trip.
  await page.reload()
  await expect(shell(page)).toBeVisible({ timeout: 15_000 })
})

test('로그아웃하면 세션이 끊긴다', async ({ page }) => {
  await page.goto('/')
  await fillAuthForm(page, 'login', ADMIN)
  await expect(shell(page)).toBeVisible({ timeout: 15_000 })
  await page.goto('/settings')
  await page.getByRole('button', { name: '로그아웃' }).click()
  await expect(page.getByRole('button', { name: '회원가입', exact: true })).toBeVisible({
    timeout: 10_000,
  })
  await page.reload()
  await expect(page.getByRole('button', { name: '회원가입', exact: true })).toBeVisible({
    timeout: 10_000,
  })
})
