import { expect, type Page } from '@playwright/test'

/** Account shared by the UI suites. The first signup on an empty database is an
 *  active administrator, which the persona tests need. */
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

/** Signs in as the shared admin, creating it on first run. Retries the login
 *  after a 409, so concurrent workers racing an empty database all succeed. */
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
 *
 * Safe to call on a page already signed in as somebody else: a signup that
 * lands in `pending` is issued no session, so the caller's cookies survive.
 * The password is a parameter because a caller that afterwards signs *into*
 * the account has to know which one the row was created with, whether this
 * call made it or found it.
 */
export async function seedPendingUser(
  page: Page,
  email = 'e2e-pending@example.com',
  password = 'pending-playwright-pass',
): Promise<string> {
  await page.request.post('/api/auth/signup', {
    data: { email, password, name: '승인 대기' },
    failOnStatusCode: false,
  })
  return email
}

/**
 * Opens the sidebar on viewports where it starts collapsed.
 *
 * The wait on the toggle is not politeness — it is the whole correctness of
 * this. Asked the instant a navigation resolves, `isVisible()` says no because
 * nothing is drawn yet, and on a desktop, where the sidebar was already open,
 * the answer was to press the toggle and *close* it. Waiting for the shell
 * first means the question is asked of a rendered page.
 */
export async function openSidebar(page: Page) {
  const toggle = page.getByRole('button', { name: '사이드바 토글' })
  await expect(toggle).toBeVisible({ timeout: 20_000 })
  // The toggle walks full → rail → hidden, so reaching the full panel can take
  // more than one press. 검색 is the probe because it is in the full panel and
  // in neither of the other two.
  const probe = page.getByLabel('대화 빠른 검색')
  for (let i = 0; i < 3; i++) {
    if (await probe.isVisible().catch(() => false)) break
    await toggle.click()
    await page.waitForTimeout(350)
  }
  await expect(probe).toBeVisible({ timeout: 10_000 })
}

/** 에이전트·스킬·커넥터·메모리·디자인·대화 관리 live in the account menu. */
export async function gotoWorkspace(page: Page, name: string) {
  await openSidebar(page)
  await page.getByRole('button', { name: '계정 메뉴' }).first().click()
  await page.getByRole('menuitem', { name, exact: true }).first().click()
}

export async function gotoSurface(page: Page, kind: string) {
  await page.goto(`/new/${kind}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
}

/**
 * The assistant's answer, scoped to its `<p>`. Tests echo a token back to tell
 * what the model remembered, and the conversation title is generated from that
 * same answer — unscoped, the sidebar title matches even when the model never
 * answered. Titles are `<span>`, user messages `<div>`.
 */
export function answerText(page: Page, text: string | RegExp) {
  return page.locator('p').filter({ hasText: text }).first()
}

/**
 * Picks a conversation model that has tools.
 *
 * The screen default is a strict-local model, and a strict-local turn is given
 * only the two built-ins that never leave this process — no web search, no
 * code execution, no connectors. Any spec whose subject is a tool has to say
 * which model it means.
 *
 * Chosen by excluding the Strict Local group rather than by naming an id: a
 * picker row prints the model's name, not its route.
 */
export async function pickToolModel(page: Page, name = /qwen3\.6/i) {
  await page
    .getByRole('button', { name: /qwen|glm|claude|gpt|gemini|grok|deepseek|kimi|hy3|mimo/i })
    .first()
    .click()
  const rows = page.getByRole('button', { name })
  const count = await rows.count()
  for (let i = 0; i < count; i++) {
    const label = (await rows.nth(i).getAttribute('aria-label')) ?? (await rows.nth(i).innerText())
    if (!/strict/i.test(label)) {
      await rows.nth(i).click()
      return
    }
  }
  throw new Error('도구를 쓸 수 있는 모델을 고르지 못했습니다')
}
