import { expect, type Page } from '@playwright/test'

/** Account shared by the UI suites. The first signup on an empty database is an
 *  active administrator, which the persona tests need.
 *
 *  `KCHAT_E2E_EMAIL` / `KCHAT_E2E_PASSWORD` point the suites at an account that
 *  already exists — running the personas against a real workspace rather than a
 *  seeded one. Naming an account also turns the signup fallback off: on an
 *  instance that already has users, a failed login must say so rather than
 *  quietly leaving a pending account behind. */
export const E2E_ADMIN = {
  email: process.env.KCHAT_E2E_EMAIL || 'e2e-personas@example.com',
  password: process.env.KCHAT_E2E_PASSWORD || 'personas-playwright-pass',
  name: process.env.KCHAT_E2E_NAME || 'E2E 관리자',
}

/** Whether the account was named from outside, and so must not be created. */
const GIVEN = Boolean(process.env.KCHAT_E2E_EMAIL)

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

  if (GIVEN) {
    throw new Error(
      `${E2E_ADMIN.email} 로 로그인하지 못했습니다. ` +
        'KCHAT_E2E_PASSWORD 를 확인하세요 — 계정을 새로 만들지는 않습니다.',
    )
  }

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

/** Sign in as a specifically named fixture account, independent of the
 * administrator selected for the rest of the suite. */
export async function signInAs(page: Page, email: string, password: string) {
  await page.context().clearCookies()
  await page.goto('/')
  await page.getByRole('button', { name: '로그인', exact: true }).first().click()
  await page.getByLabel('이메일').fill(email)
  await page.getByLabel('비밀번호').fill(password)
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/api/auth/login') && r.request().method() === 'POST',
    ),
    page.locator('form').getByRole('button', { name: '로그인', exact: true }).click(),
  ])
  await expect(page.getByRole('button', { name: '사이드바 토글' })).toBeVisible({
    timeout: 15_000,
  })
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
  // Asked by position, not by visibility. Below 1024px the closed panel is not
  // removed — it stays mounted and slides off-screen — so `isVisible()` answers
  // yes for a drawer nobody can reach, this loop presses nothing, and the first
  // click on a row fails fifteen seconds later with "element is outside of the
  // viewport". Where the panel *is* separates the two states; whether it exists
  // no longer does.
  const reachable = async () => {
    const box = await probe.boundingBox().catch(() => null)
    return Boolean(box && box.width > 0 && box.x >= 0)
  }
  for (let i = 0; i < 3; i++) {
    if (await reachable()) return
    await toggle.click()
    await page.waitForTimeout(350)
  }
  await expect(probe).toBeInViewport({ timeout: 10_000 })
}

/** 에이전트·스킬·커넥터·메모리·디자인·대화 기록 live in the account menu. */
export async function gotoWorkspace(page: Page, name: string) {
  await openSidebar(page)
  await page.getByRole('button', { name: '계정 메뉴' }).first().click()
  await page.getByRole('menuitem', { name, exact: true }).first().click()
}

/**
 * Presses a document surface through the plan it stops on.
 *
 * 슬라이드 and 보고서 do not write from the sentence they were given. They plan
 * and stop — `clarify` when they need something answered, `outline` when they
 * have the shape ready — and *nothing is stored* until a button on that card is
 * pressed. So a test that types, waits for the stream to end and then looks for
 * the document is waiting for a write that was never going to start.
 *
 * Both stages are pressed through, and the wait is on either side of each: the
 * approval opens a second stream, and that is the one the artifact comes out of.
 * Returns once a turn ends with no card left, which is the only state in which
 * there is something to export.
 *
 * `approveOnce` presses and returns straight away, for the tests that are about
 * what happens *while* the run goes; `approvePlan` keeps pressing until a turn
 * ends with no card left, which is the only state that has a finished document
 * in it.
 */
export async function approveOnce(page: Page, timeout = 480_000): Promise<boolean> {
  const stop = page.getByLabel('중지')
  // 「이대로 생성」 or 「고친 대로 생성」 — the card renames its own button the
  // moment anything in the outline is touched, and a run that edits a heading
  // then waits for the first name waits out its whole timeout beside a card
  // that is finished and asking.
  const approve = page.getByRole('button', { name: /이대로 생성|고친 대로 생성/ })
  // The unconditional one. 이대로 계속 needs every question answered first, and
  // what these tests are about is the document, not the questions.
  const carryOn = page.getByRole('button', { name: '있는 자료로 진행' })
  // The figures offer, declined. It is a second gate behind the outline — a
  // deck or a report that could carry drawings asks before spending the
  // credits — and nothing here knew the words, so a run that reached it simply
  // stopped: no card these buttons match, no stream, and a test waiting out
  // its whole timeout on an outline that was finished and asking a question.
  //
  // Declined rather than accepted because these tests are about the document.
  // Drawing costs a model call per figure and comes back different every time.
  const noFigures = page.getByRole('button', { name: '그림 없이 생성' })
  const card = approve.or(carryOn).or(noFigures).first()

  // Both questions are asked together, every half second. "Not streaming" is
  // true before a turn starts as well as after it ends, so waiting on it alone
  // can be satisfied by the wrong silence and hand the next wait a request that
  // is still being planned. And a card on screen is not yet a card that can be
  // pressed — it is drawn while the store still holds the turn that produced
  // it, and its buttons are disabled for as long as that lasts. Only both
  // conditions together mean there is something here to press.
  const deadline = Date.now() + timeout
  let quiet = 0
  while (Date.now() < deadline) {
    const streaming = await stop.isVisible().catch(() => false)
    if (!streaming && (await card.isVisible().catch(() => false))) break
    // ~15s with neither a stream nor a card: this surface does not propose, or
    // the proposal has already been dealt with. Neither is a failure.
    quiet = streaming ? 0 : quiet + 1
    if (quiet >= 30) return false
    await page.waitForTimeout(500)
  }

  // In the order they are offered, so a screen showing both answers the
  // outline first and meets the figures offer on the next round.
  let button = carryOn
  for (const candidate of [approve, carryOn, noFigures]) {
    if (await candidate.isVisible().catch(() => false)) {
      button = candidate
      break
    }
  }
  if (!(await button.isVisible().catch(() => false))) return false
  await expect(button).toBeEnabled({ timeout: 30_000 })
  // Pressed after the card has stopped moving.
  //
  // The proposal arrives on a stream that is still going: the card fades up,
  // steps land under it and the transcript scrolls, and a press aimed at the
  // button's first position lands on a node that has since been replaced —
  // "element was detached from the DOM" after fifteen seconds of retrying.
  // Measured once it settles, the button holds one identity and one position
  // for as long as you care to watch, so the wait is short and the click is
  // certain.
  const settle = async () => {
    let last = ''
    for (let i = 0; i < 20; i++) {
      const box = await button.boundingBox().catch(() => null)
      const now = box ? `${Math.round(box.x)},${Math.round(box.y)}` : ''
      if (now && now === last) return
      last = now
      await page.waitForTimeout(250)
    }
  }
  await settle()
  await button.click()
  // Returns as soon as it is pressed. Waiting for the card to go would wait out
  // the run it just started — the card is drawn from the stored plan and only
  // clears when that plan has been written — and the callers that use this
  // directly are the ones whose subject is what happens *while* it runs.
  return true
}

export async function approvePlan(page: Page, timeout = 480_000) {
  // clarify can precede outline, so more than one press may be needed. Bounded,
  // because a card that never goes away is a failure to report rather than a
  // loop to keep running.
  const card = page
    .getByRole('button', { name: /이대로 생성|고친 대로 생성/ })
    .or(page.getByRole('button', { name: '있는 자료로 진행' }))
    .or(page.getByRole('button', { name: '그림 없이 생성' }))
    .first()
  // Up to four presses: clarify, outline, figures — and one spare.
  for (let round = 0; round < 4; round++) {
    if (!(await approveOnce(page, timeout))) return
    // Only here. Coming straight back round would find the same card — still up
    // because the run it started has not finished — and press it again, and the
    // turns that produces are a second and third document nobody asked for.
    // Clarification buttons can remain in the historical message while the
    // next outline card is already actionable. Waiting the whole document
    // timeout on that old node made a healthy two-step approval spend eight
    // minutes idle and then time out. Give the transition enough time to
    // settle, then re-run `approveOnce`, which deliberately prefers the new
    // outline action over the older carry-on action.
    await card.waitFor({ state: 'hidden', timeout: Math.min(timeout, 20_000) }).catch(() => undefined)
  }
  throw new Error('제안 카드가 네 번을 눌러도 사라지지 않았습니다.')
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
// 기본값이 3.5 를 앞세우는 이유: 35b 는 검색·아티팩트 도구를 가끔 건너뛰고
// 본문으로 답한다. 도구 스펙이 재는 것은 도구 경로이지 모델의 변덕이 아니다.
export async function pickToolModel(page: Page, name = /qwen3\.5|qwen3\.6/i) {
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

/**
 * Opens a report, then writes into the one that opened.
 *
 * The other way round does not work. Choosing an artifact from the API and
 * then finding its card meant matching on a title, and the shared account has
 * five reports called the same thing — so a spec could seed one document and
 * open another, and the failure looks like a feature that does not work rather
 * than a document that was never touched. Opening first removes the matching
 * problem entirely: the session in the URL says which document is on screen,
 * and that is the one written into.
 *
 * Returns the artifact and the id of the section the body went into, so a
 * caller can scope its assertions to what it wrote rather than to whatever
 * else the document is carrying from other runs.
 */
export async function openAndSeedReport(
  page: Page,
  body: string,
  options: { clearDiagrams?: boolean } = {},
): Promise<{ id: string; sectionId: string }> {
  await signIn(page)
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^보고서/ }).click()
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  await expect(page.locator('[data-panel="artifact"]')).toBeVisible({ timeout: 30_000 })
  // Idle first. The card opened is whichever report comes first, and in a full
  // suite run that can be one another spec is still writing — the panel then
  // shows a document mid-stream, the seeded body is hidden behind it, and the
  // failure reads as a block that will not render.
  await expect(page.getByLabel('중지')).toBeHidden({ timeout: 300_000 })

  const sessionId = page.url().split('/s/')[1]
  const seeded = await page.evaluate(
    async ([admin, content, session, clear]: [typeof E2E_ADMIN, string, string, boolean]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: admin.email, password: admin.password }),
      })
      const s = await login.json()
      const headers = {
        'content-type': 'application/json',
        Authorization: `Bearer ${s.accessToken ?? s.access_token}`,
      }
      const row = await (
        await fetch(`/api/sessions/${session}`, { headers, credentials: 'include' })
      ).json()
      const id: string = row.artifactId ?? row.artifact_id
      if (!id) return { ok: false, why: '이 대화에 문서가 없습니다', id: '', sectionId: '' }
      const full = await (
        await fetch(`/api/artifacts/${id}`, { headers, credentials: 'include' })
      ).json()
      const data = full.data ?? full
      // One section, and Markdown.
      //
      // The rest of the document went with it. A report left over from a 서식
      // run carries `format: "html"` sections, and a document with one of
      // those in it opens in the page view — so a case about the web view
      // found its own text present and hidden, and read that as a block that
      // would not render. The seeded document is now only what the seed put
      // there.
      data.sections = [{ ...data.sections[0], content, format: 'markdown' }]
      data.templateId = ''
      data.reviewComments = []
      // A picture stored by an earlier run would be shown instead of a fresh
      // render, and some callers are about what the renderer does.
      if (clear) data.sections[0].diagrams = {}
      const res = await fetch(`/api/artifacts/${id}`, {
        method: 'PATCH',
        headers,
        credentials: 'include',
        body: JSON.stringify({ data }),
      })
      return {
        ok: res.status === 200,
        why: String(res.status),
        id,
        sectionId: String(data.sections[0].id),
      }
    },
    [E2E_ADMIN, body, sessionId, options.clearDiagrams === true] as [
      typeof E2E_ADMIN,
      string,
      string,
      boolean,
    ],
  )
  expect(seeded.ok, `씨앗 심기 실패: ${seeded.why}`).toBe(true)

  // Reloaded rather than re-navigated: the panel is holding the copy it was
  // handed, and the body just written is not in it.
  await page.reload()
  await expect(page.locator('[data-panel="artifact"]')).toBeVisible({ timeout: 30_000 })
  return { id: seeded.id, sectionId: seeded.sectionId }
}

/**
 * Whether this workspace has a surface switched on.
 *
 * `image` and `av` default to off — they spend credits per generation, so an
 * administrator turns them on deliberately — and a screen for a surface that
 * is off is an EmptyState saying so, with no composer on it. A spec that walks
 * one of those screens then fails on a missing 서식 고르기 and reads as a
 * broken feature, which is the one thing it is not.
 */
export async function surfaceOn(page: Page, kind: string): Promise<boolean> {
  await page.goto(`/new/${kind}`)
  const composer = page.getByLabel('프롬프트 입력')
  // `getByText`, because `EmptyState` writes its title as a `<p>` — there is
  // no heading here to ask for.
  const off = page.getByText(/기능이 꺼져 있습니다/)
  await expect(composer.or(off).first()).toBeVisible({ timeout: 20_000 })
  return (await composer.count()) > 0
}

/**
 * The artifacts this account holds, read through the API rather than the UI.
 *
 * An artifact is only an artifact if it was *stored*: a document that appears
 * in the panel and is gone on the next login was a message with a border round
 * it. The gallery would answer that too, and slowly and through three
 * selectors — this asks the server, which is where the claim actually lives.
 *
 * The credentials come from `E2E_ADMIN`, so a suite pointed at another account
 * by `KCHAT_E2E_EMAIL` reads that account's shelf instead of failing to log in
 * as somebody else.
 */
export async function storedArtifacts(page: Page, kind?: string) {
  return await page.evaluate(
    async ({ email, password, kind }) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const { accessToken } = await login.json()
      const headers = { Authorization: `Bearer ${accessToken}` }
      const rows = await (await fetch('/api/artifacts', { headers })).json()
      const list: { id: string; kind: string }[] = Array.isArray(rows) ? rows : rows.items
      const wanted = kind ? list.filter((a) => a.kind === kind) : list
      // The listing sends documents without their markup, so each one is
      // fetched whole — what is being asserted is usually what is inside.
      return await Promise.all(
        wanted
          .slice(0, 5)
          .map(async (row) => await (await fetch('/api/artifacts/' + row.id, { headers })).json()),
      )
    },
    { email: E2E_ADMIN.email, password: E2E_ADMIN.password, kind },
  )
}

/**
 * Every artifact id this account holds, whatever kind it is.
 *
 * Counting one kind is how a check passes for the wrong reason: a mail draft
 * refused as `html` and stored as `code` leaves an `html` count unchanged, and
 * the assertion reads as "no document was made" while one sits in the panel.
 * Ids rather than a number, so "one added and one removed" cannot look like
 * nothing happening either.
 */
export async function artifactIds(page: Page): Promise<string[]> {
  return await page.evaluate(
    async ({ email, password }) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const { accessToken } = await login.json()
      const rows = await (
        await fetch('/api/artifacts', { headers: { Authorization: `Bearer ${accessToken}` } })
      ).json()
      const list: { id: string }[] = Array.isArray(rows) ? rows : rows.items
      return list.map((row) => row.id)
    },
    { email: E2E_ADMIN.email, password: E2E_ADMIN.password },
  )
}

/**
 * 결과물 패널이 준비될 때까지 기다린다.
 *
 * 여러 스펙이 「내보내기」 버튼이 보이는 것을 준비 신호로 썼다. That button
 * lives inside the ribbon's 파일 tab, so the wait was really "is the panel
 * open *and* on that tab" — and it broke the day the ribbon grouped its
 * commands, in a dozen specs at once, reporting a missing export button for
 * documents that had exported fine. The panel and a settled run are what
 * those tests actually meant.
 */
export async function artifactReady(page: Page, timeout = 480_000) {
  // 패널이거나, 갤러리가 여는 미리보기 대화상자거나. Both carry the ribbon;
  // only the panel carries `data-panel`.
  await expect(
    page.locator('[data-panel="artifact"], [role="dialog"] [role="tablist"]').first(),
  ).toBeVisible({ timeout })
  await expect(page.getByLabel('중지')).toBeHidden({ timeout })
}

/** Opens one tab of an artifact panel's ribbon by name. */
export async function ribbonTab(page: Page, name: string) {
  await page.getByRole('tab', { name, exact: true }).click()
}
