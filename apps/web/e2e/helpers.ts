import { expect, type Page } from '@playwright/test'

/**
 * The account shared by the UI coverage suites.
 *
 * On an empty database the first signup is an active administrator, which is
 * what the persona tests need because they walk into the admin screens. If the
 * account already exists, this takes the sign-in path instead.
 */
export const E2E_ADMIN = {
  email: 'e2e-personas@example.com',
  password: 'personas-playwright-pass',
  name: 'E2E 관리자',
}

async function submitAuthForm(page: Page, mode: 'login' | 'signup') {
  const form = page.locator('form')
  await page
    .getByRole('button', { name: mode === 'login' ? '로그인' : '회원가입', exact: true })
    .first()
    .click()
  if (mode === 'signup') await page.getByLabel('이름').fill(E2E_ADMIN.name)
  await page.getByLabel('이메일').fill(E2E_ADMIN.email)
  await page.getByLabel('비밀번호').fill(E2E_ADMIN.password)
  // Navigating away while the POST is in flight cancels it and the session
  // cookie never lands, so wait for the response rather than the click.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/auth/${mode}`) && r.request().method() === 'POST',
    ),
    form.getByRole('button', { name: mode === 'login' ? '로그인' : '가입 요청' }).click(),
  ])
}

/**
 * Signs in as the shared admin, creating it on first run.
 *
 * These specs run in parallel workers, so several can reach an empty database at
 * once: one signup wins and the others get 409. Retrying the login afterwards is
 * what makes the helper safe to call from every worker's `beforeEach`.
 */
export async function signIn(page: Page) {
  await page.goto('/')
  const shell = page.getByRole('button', { name: '사이드바 토글' })
  if (await shell.isVisible().catch(() => false)) return

  await submitAuthForm(page, 'login')
  if (await shell.isVisible({ timeout: 5_000 }).catch(() => false)) return

  await submitAuthForm(page, 'signup')
  if (await shell.isVisible({ timeout: 5_000 }).catch(() => false)) return

  // Signup lost the race — the account exists now, so log into it.
  await submitAuthForm(page, 'login')

  // On an instance that already has an admin, a fresh signup lands in `pending`
  // and never reaches the shell. That is correct product behaviour and a broken
  // test fixture, so say which it is instead of timing out on a locator.
  const pending = page.getByRole('heading', { name: '승인을 기다리는 중입니다' })
  if (await pending.isVisible({ timeout: 3_000 }).catch(() => false)) {
    throw new Error(
      `${E2E_ADMIN.email} 계정이 승인 대기 상태입니다. ` +
        '`bash scripts/e2e-seed.sh` 를 먼저 실행해 계정을 승인하세요.',
    )
  }
  await expect(shell).toBeVisible({ timeout: 15_000 })
}

/**
 * Creates an account that stays `pending`, for the admin approval screens.
 * Idempotent: a duplicate signup answers 409 and that is fine.
 */
export async function seedPendingUser(
  page: Page,
  email = 'e2e-pending@example.com',
): Promise<string> {
  await page.request.post('/api/auth/signup', {
    data: { email, password: 'pending-playwright-pass', name: '승인 대기' },
    failOnStatusCode: false,
  })
  return email
}

/** Opens the sidebar on viewports where it starts collapsed. */
export async function openSidebar(page: Page) {
  const nav = page.getByRole('link', { name: '커넥터' })
  if (!(await nav.isVisible().catch(() => false))) {
    await page.getByRole('button', { name: '사이드바 토글' }).click()
  }
  await expect(nav).toBeVisible()
}

export async function gotoSurface(page: Page, kind: string) {
  await page.goto(`/new/${kind}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
}

/**
 * The assistant's answer, scoped to the rendered paragraph.
 *
 * These tests make the model echo a token back verbatim — the only way to tell
 * what it remembered from what it already knew. But the conversation title is
 * generated from that same answer, so the token also appears in the sidebar
 * and the header. Unscoped, it matches in three places, and `.first()` picks
 * the sidebar title — which passes even when the model never answered.
 *
 * Answers are `<p>`, titles are `<span>` and user messages are `<div>`, so
 * scoping to the paragraph is enough.
 */
export function answerText(page: Page, text: string | RegExp) {
  return page.locator('p').filter({ hasText: text }).first()
}
