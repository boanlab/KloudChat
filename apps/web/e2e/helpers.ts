import { expect, type Page } from '@playwright/test'

/** Account shared by the UI suites; the first signup on an empty database is an active admin.
 *  `KCHAT_E2E_EMAIL` / `KCHAT_E2E_PASSWORD` name an existing account instead and disable the signup fallback. */
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
  // Wait for the response, not the click: navigating mid-POST drops the session cookie.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/auth/${mode}`) && r.request().method() === 'POST',
    ),
    form.getByRole('button', { name: mode === 'login' ? '로그인' : /^(가입 요청|가입하기)$/ }).click(),
  ])
}

/** Signs in as the shared admin, creating it on first run; retries login after a signup race. */
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

  // Signup lost the race: log into the existing account.
  await submitAuthForm(page, 'login')

  // On an instance that already has an admin a fresh signup lands in `pending`; say so.
  const pending = page.getByRole('heading', { name: '승인을 기다리는 중입니다' })
  if (await pending.isVisible({ timeout: 3_000 }).catch(() => false)) {
    throw new Error(
      `${E2E_ADMIN.email} 계정이 승인 대기 상태입니다. ` +
        '`bash scripts/e2e-seed.sh` 를 먼저 실행해 계정을 승인하세요.',
    )
  }
  await expect(shell).toBeVisible({ timeout: 15_000 })
}

/** Signs in as a named fixture account, replacing the current session. */
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

/** Creates an account that stays `pending` (idempotent; a duplicate signup answers 409).
 *  Issues no session, so a signed-in caller keeps its cookies. Returns the email. */
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

/** Opens the sidebar on viewports where it starts collapsed.
 *  Waits for the shell first, so the visibility question is asked of a rendered page. */
export async function openSidebar(page: Page) {
  const toggle = page.getByRole('button', { name: '사이드바 토글' })
  await expect(toggle).toBeVisible({ timeout: 20_000 })
  // The toggle cycles full → rail → hidden; 대화 빠른 검색 exists only in the full panel.
  const probe = page.getByLabel('대화 빠른 검색')
  // Below 1024px the closed drawer stays mounted off-screen, so test position, not visibility.
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

/** Presses one proposal card (clarify, outline or figures) and returns straight away.
 *  슬라이드 and 보고서 store nothing until a card is pressed; the approval opens the stream the artifact comes from.
 *  Returns false when ~15s pass with neither a stream nor a card. */
export async function approveOnce(page: Page, timeout = 480_000): Promise<boolean> {
  const stop = page.getByLabel('중지')
  // The card renames its button once the outline has been edited.
  const approve = page.getByRole('button', { name: /이대로 생성|고친 대로 생성/ })
  // Unconditional; 이대로 계속 needs every question answered first.
  const carryOn = page.getByRole('button', { name: '있는 자료로 진행' })
  // Figures offer, declined: each figure costs a model call and differs every run.
  const noFigures = page.getByRole('button', { name: '그림 없이 생성' })
  const card = approve.or(carryOn).or(noFigures).first()

  // Both conditions together: "not streaming" also holds before a turn starts, and a
  // card's buttons stay disabled while the store still holds the turn that drew it.
  const deadline = Date.now() + timeout
  let quiet = 0
  while (Date.now() < deadline) {
    const streaming = await stop.isVisible().catch(() => false)
    if (!streaming && (await card.isVisible().catch(() => false))) break
    // ~15s with neither a stream nor a card: nothing to press.
    quiet = streaming ? 0 : quiet + 1
    if (quiet >= 30) return false
    await page.waitForTimeout(500)
  }

  // In the order offered, so a screen showing both answers the outline first.
  let button = carryOn
  for (const candidate of [approve, carryOn, noFigures]) {
    if (await candidate.isVisible().catch(() => false)) {
      button = candidate
      break
    }
  }
  if (!(await button.isVisible().catch(() => false))) return false
  await expect(button).toBeEnabled({ timeout: 30_000 })
  // Click once the button stops moving: the card animates while its stream is still going.
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
  // Not waiting for the card to clear: that would wait out the run just started.
  return true
}

/** Presses proposal cards until a turn ends with no card left. */
export async function approvePlan(page: Page, timeout = 480_000) {
  // clarify can precede outline; bounded, since a card that never clears is a failure.
  const card = page
    .getByRole('button', { name: /이대로 생성|고친 대로 생성/ })
    .or(page.getByRole('button', { name: '있는 자료로 진행' }))
    .or(page.getByRole('button', { name: '그림 없이 생성' }))
    .first()
  // Up to four presses: clarify, outline, figures, one spare.
  for (let round = 0; round < 4; round++) {
    if (!(await approveOnce(page, timeout))) return
    // Let the pressed card clear before the next round, or it is pressed twice. Short wait:
    // a stale clarify button can stay in history while the next outline card is already live.
    await card.waitFor({ state: 'hidden', timeout: Math.min(timeout, 20_000) }).catch(() => undefined)
  }
  throw new Error('제안 카드가 네 번을 눌러도 사라지지 않았습니다.')
}

export async function gotoSurface(page: Page, kind: string) {
  await page.goto(`/new/${kind}`)
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
}

/** The assistant's answer, scoped to `<p>`: sidebar titles are `<span>`, user messages `<div>`. */
export function answerText(page: Page, text: string | RegExp) {
  return page.locator('p').filter({ hasText: text }).first()
}

/** Picks a conversation model outside the Strict Local group (strict-local turns get no
 *  web search, code execution or connectors). Matched by name: picker rows print names, not ids.
 *  The default prefers 3.5/3.6 because 35b sometimes answers without calling tools. */
export async function pickToolModel(page: Page, name = /qwen3\.8/i) {
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

/** Opens the first report in the gallery, then overwrites it with one Markdown section via the API.
 *  Opened first because titles are not unique; the session in the URL names the document.
 *  Returns the artifact id and the seeded section id. */
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
  // Idle first: the opened report may still be mid-stream from another spec.
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
      // One Markdown section only: any `html` section would open the page view instead.
      data.sections = [{ ...data.sections[0], content, format: 'markdown' }]
      data.templateId = ''
      data.reviewComments = []
      // A stored diagram would be shown instead of a fresh render.
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

  // Reload: the panel holds the copy it was handed.
  await page.reload()
  await expect(page.locator('[data-panel="artifact"]')).toBeVisible({ timeout: 30_000 })
  return { id: seeded.id, sectionId: seeded.sectionId }
}

/** Whether a surface is switched on. `image` and `av` default to off and show an EmptyState with no composer. */
export async function surfaceOn(page: Page, kind: string): Promise<boolean> {
  await page.goto(`/new/${kind}`)
  const composer = page.getByLabel('프롬프트 입력')
  // `EmptyState` writes its title as a `<p>`, not a heading.
  const off = page.getByText(/기능이 꺼져 있습니다/)
  await expect(composer.or(off).first()).toBeVisible({ timeout: 20_000 })
  return (await composer.count()) > 0
}

/** This account's stored artifacts via the API (first five, fetched whole), optionally by kind. */
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
      // The listing omits markup, so each row is fetched whole.
      return await Promise.all(
        wanted
          .slice(0, 5)
          .map(async (row) => await (await fetch('/api/artifacts/' + row.id, { headers })).json()),
      )
    },
    { email: E2E_ADMIN.email, password: E2E_ADMIN.password, kind },
  )
}

/** Every artifact id this account holds, whatever the kind. */
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

/** Waits for an artifact panel (or the gallery's preview dialog) and an idle run. */
export async function artifactReady(page: Page, timeout = 480_000) {
  // Both carry the panel header's quick tools; only the panel carries `data-panel`.
  await expect(
    page.locator('[data-panel="artifact"], [role="dialog"] [aria-label="빠른 도구"]').first(),
  ).toBeVisible({ timeout })
  await expect(page.getByLabel('중지')).toBeHidden({ timeout })
}

/** The report or slide panel's header row (reading mode: title, view, 검토, 내려받기, 편집, ⋯). */
export function panelHeader(page: Page) {
  return page.locator('[data-panel="artifact"] header, [role="dialog"] header').first()
}

/** Turns the panel's 「편집」 on or off; the ribbon shows only while it is on. */
export async function editMode(page: Page, on = true) {
  const toggle = panelHeader(page).getByRole('button', { name: '편집', exact: true })
  if (((await toggle.getAttribute('aria-pressed')) === 'true') !== on) await toggle.click()
}

/** Picks an item of the header's ⋯ menu: 목차, 원문 편집, 버전 기록, 이걸로 새로 시작 … */
export async function moreMenu(page: Page, item: string | RegExp) {
  await panelHeader(page).getByRole('button', { name: '더보기' }).click()
  await page.locator('[role="menu"]:visible').getByRole('menuitem', { name: item }).first().click()
}

/** Opens one tab of an artifact panel's editing ribbon, switching 「편집」 on first. */
export async function ribbonTab(page: Page, name: string) {
  await editMode(page)
  await page.getByRole('tab', { name, exact: true }).click()
}

// ── the web slides panel (PowerPoint-editor-like: always editable on a desktop, saved with 저장)

/** The web slides panel, in the side panel or in the gallery's preview dialog. */
export function deckPanel(page: Page) {
  return page.locator('[data-deck-panel]').first()
}

/** The deck panel's document header: title · vN · 저장 안 됨 · 저장 · 내려받기 · 공유 · 더보기. */
export function deckHeader(page: Page) {
  return deckPanel(page).locator('header').first()
}

/** The editor region: rail, stage, notes and the strips over the slide. */
export function deckEditor(page: Page) {
  return deckPanel(page).getByRole('region', { name: '웹 슬라이드 편집기' })
}

/** The one toolbar row (absent on a phone and while the deck is being written). */
export function deckToolbar(page: Page) {
  return deckPanel(page).getByRole('toolbar', { name: '슬라이드 도구' })
}

/** The rail's numbered thumbnails (`aria-label` 「N번 장」, `aria-current` on the slide shown). */
export function deckThumbs(page: Page) {
  return deckEditor(page).locator('.kc-thumb')
}

/** The slide shown, on the stage. */
export function deckStage(page: Page) {
  return deckEditor(page).locator('.kc-canvas')
}

/** The speaker notes of the slide shown; they commit on blur. */
export function deckNotes(page: Page) {
  return deckEditor(page).getByRole('textbox', { name: '발표자 노트' })
}

/** Opens a toolbar menu (레이아웃, 이미지, 테마 …) and returns it. */
export async function deckToolbarMenu(page: Page, name: string) {
  await deckToolbar(page).getByRole('button', { name, exact: true }).click()
  return page.getByRole('menu', { name, exact: true })
}

/** Picks a layout from the toolbar's 레이아웃 menu, by its whole label (「카드」, not 「카드 3개」). */
export async function deckLayout(page: Page, layout: string | RegExp) {
  const menu = await deckToolbarMenu(page, '레이아웃')
  await menu.getByRole('menuitemradio', { name: layout, exact: true }).first().click()
}

/** Selects an element on the stage (`table`, `metrics`, `cards`, `chart`, `image`, `title`, `content`). */
export async function deckSelect(page: Page, element: string) {
  // Elements take the selection on pointer down, as the PowerPoint editor's shapes do.
  await deckStage(page).locator(`[data-slide-element="${element}"]`).first().click()
}

/** The editor shown for the selected element (table rows, chart series, picture caption). */
export function deckSelection(page: Page) {
  return deckEditor(page).getByRole('region', { name: '선택한 요소' })
}

/** Presses 저장 in the header and waits for the edits to be saved. */
export async function saveDeck(page: Page) {
  const active = page.locator(':focus')
  // a box being typed in, or the notes, commit as they are left
  if (await active.count()) await active.first().blur().catch(() => undefined)
  await deckHeader(page).getByRole('button', { name: '저장', exact: true }).click()
  await expect(deckHeader(page).getByText('저장 안 됨')).toHaveCount(0, { timeout: 30_000 })
}

/** Opens the 검토 pane (the toolbar's 검토, or 더보기 › 검토 where there is no toolbar). */
export async function deckReview(page: Page) {
  const pane = deckPanel(page).getByRole('complementary', { name: '검토 메모' })
  if (await pane.isVisible().catch(() => false)) return pane
  const button = deckToolbar(page).getByRole('button', { name: /^검토/ })
  if (await button.isVisible().catch(() => false)) await button.click()
  else {
    await deckHeader(page).getByRole('button', { name: '더보기' }).click()
    await page.locator('[role="menu"]:visible').getByRole('menuitem', { name: '검토' }).first().click()
  }
  await expect(pane).toBeVisible()
  return pane
}

/** The title box of the slide shown, typed into in place. */
export function deckTitle(page: Page) {
  return deckStage(page).locator('[data-slide-element="title"]').first()
}

/** Replaces the text of a box on the slide and leaves it, which commits it to the working copy. */
export async function deckType(box: ReturnType<Page['locator']>, text: string) {
  await box.click()
  await box.fill(text)
  await box.blur()
}

/** Sets the type size (% of the selection's own size, or the slide's) from the toolbar, folded or not. */
export async function deckTextSize(page: Page, percent: number) {
  const toolbar = deckToolbar(page)
  const folded = toolbar.getByRole('button', { name: '글꼴과 크기', exact: true })
  // narrow, the font and size controls fold into one menu
  if (await folded.isVisible().catch(() => false)) await folded.click()
  await toolbar.getByRole('button', { name: '글꼴 크기', exact: true }).click()
  await page.getByRole('menu', { name: '글꼴 크기', exact: true }).getByRole('button', { name: `${percent}%`, exact: true }).click()
}
