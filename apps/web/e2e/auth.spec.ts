/**
 * Authentication against a real backend.
 *
 * Requires the API running and an empty `users` table — the first signup is
 * what creates the administrator.
 *
 * Run: npx playwright test e2e/auth.spec.ts --project=desktop
 */

import { expect, test } from '@playwright/test'
import { seedPendingUser } from './helpers'

const ADMIN = { email: 'e2e-admin@example.com', password: 'correct-horse-battery', name: '관리자' }
// Unique per run: the pending-approval path only exists for an account that has
// never been approved, so reusing one address makes the test pass once and then
// report a regression that is really just leftover state.
const USER = {
  email: `e2e-user-${Date.now().toString(36)}@example.com`,
  password: 'another-long-password',
  name: '학생',
}

// Signup order is load-bearing (first account becomes admin), so these share
// state and must not interleave.
test.describe.configure({ mode: 'serial' })

/**
 * What "signed in" looks like at every width.
 *
 * A sidebar link is a claim about the viewport rather than about the session:
 * below 1024px the sidebar is a drawer that starts closed, so 아티팩트 is not
 * in the page at all and a passing session reads as a lost one. The shell's
 * toggle is drawn for exactly the people who have a session and nobody else.
 */
const shell = (page: import('@playwright/test').Page) =>
  page.getByRole('button', { name: '사이드바 토글' })

async function fillAuthForm(
  page: import('@playwright/test').Page,
  mode: 'login' | 'signup',
  who: { email: string; password: string; name?: string },
) {
  // The mode tabs and the submit button share the label "로그인" ("sign in"),
  // so the tab is taken from outside the form and the submit from inside it.
  const form = page.locator('form')
  await page
    .getByRole('button', { name: mode === 'login' ? '로그인' : '회원가입', exact: true })
    .first()
    .click()
  if (mode === 'signup') await page.getByLabel('이름').fill(who.name ?? '')
  await page.getByLabel('이메일').fill(who.email)
  await page.getByLabel('비밀번호').fill(who.password)

  // Wait for the request to land before returning. Navigating while it is still
  // in flight cancels it, and the Set-Cookie never arrives — which looks exactly
  // like a broken session on the next page load.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/auth/${mode}`) && r.request().method() === 'POST',
    ),
    form.getByRole('button', { name: mode === 'login' ? '로그인' : '가입 요청' }).click(),
  ])
}

test('첫 가입 계정은 관리자로 바로 입장한다', async ({ page }) => {
  await page.goto('/')
  await fillAuthForm(page, 'signup', ADMIN)

  // Bootstrap happens once per database. On an instance that already ran this
  // suite the account exists (409) or lands in `pending` — both mean "not a
  // fresh database", which is a precondition rather than a defect.
  const pending = page.getByRole('heading', { name: '승인을 기다리는 중입니다' })
  const taken = page.getByText('이미 사용 중인 이메일입니다.')
  const notFresh = await Promise.race([
    pending.waitFor({ timeout: 6_000 }).then(() => true),
    taken.waitFor({ timeout: 6_000 }).then(() => true),
  ]).catch(() => false)
  if (notFresh) {
    test.skip(true, '빈 DB 가 아닙니다 — 부트스트랩은 첫 가입에서만 검증됩니다.')
  }

  // Landing on the home page means active + authenticated.
  await expect(page.getByRole('link', { name: '아티팩트' })).toBeVisible({ timeout: 15_000 })
})

test('모델 선택기가 LiteLLM 카탈로그를 보여 준다', async ({ page }) => {
  await page.goto('/')
  await fillAuthForm(page, 'login', ADMIN)
  await page.goto('/new/chat')
  // The picker must be showing the live catalogue, not something seeded. Naming
  // one model breaks whenever the lineup changes, so check the shape only a
  // real `/api/models`
  // response has: several entries, each with the per-1k pricing line the
  // catalogue carries.
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

  // The account approved below was the one the previous test signed up, and
  // nothing said so. Run on its own — which is what happens the moment anybody
  // re-runs a single failure by name — this logged in as an address that had
  // never been created and reported "이메일 또는 비밀번호가 올바르지 않습니다",
  // which reads as a broken login rather than a missing fixture. Seeding it
  // here answers 409 when that test did run and creates the row when it did
  // not; either way the password is the one the login below sends.
  await seedPendingUser(waitingPage, USER.email, USER.password)

  await waitingPage.goto('/')
  await fillAuthForm(waitingPage, 'login', USER)
  await expect(waitingPage.getByRole('heading', { name: '승인을 기다리는 중입니다' })).toBeVisible({
    timeout: 15_000,
  })

  await adminPage.goto('/')
  await fillAuthForm(adminPage, 'login', ADMIN)
  await adminPage.goto('/admin/users')
  // Named, because the failure it replaces was a button that never appeared:
  // on an instance where this account signed up after bootstrap it is a plain
  // user, the admin screen never renders, and the timeout says nothing about
  // why. `scripts/e2e-seed.sh` is what grants the role.
  await expect(
    adminPage.getByPlaceholder('이름 또는 이메일'),
    `${ADMIN.email} 에 관리자 권한이 없습니다 — scripts/e2e-seed.sh 를 실행하세요`,
  ).toBeVisible({ timeout: 15_000 })
  // Filtered: the table pages at forty rows, and a fresh signup does not sort
  // to the top of an instance that has been running for a while.
  await adminPage.getByPlaceholder('이름 또는 이메일').fill(USER.email)
  const row = adminPage.locator('tr', { hasText: USER.email })
  await expect(row).toBeVisible({ timeout: 15_000 })
  // Exact, because the row also carries a "<이름> 모델 제한" button and the
  // account's own name is part of that label — an unanchored "승인" matches
  // both the moment somebody is called 승인 대기.
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
  // The access token is memory-only — surviving this proves the refresh cookie
  // round-trip in bootstrap() works.
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
