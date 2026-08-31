/**
 * Regressions from real use.
 *
 * Run with: npx playwright test e2e/regress.spec.ts --project=desktop
 */

import { expect, test } from '@playwright/test'
import { E2E_ADMIN, approvePlan, openSidebar, pickToolModel, seedPendingUser, signIn, surfaceOn } from './helpers'

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

const pickLocal = pickToolModel


test('웹 검색을 켜도 화면이 살아 있다 (스텝 렌더 크래시)', async ({ page }) => {
  // A search turn behind a busy queue is minutes, not seconds.
  test.setTimeout(420_000)
  const crashes: string[] = []
  page.on('pageerror', (e) => crashes.push(e.message))

  await page.goto('/new/chat')
  await pickLocal(page)
  await page.getByRole('button', { name: '웹 검색' }).first().click()
  // The prompt has to *require* a lookup. The toggle offers the tool; it does
  // not force a call, and a general question is answerable from
  // parameters — so the turn finished in under a minute with no search step and
  // the test read that as a regression.
  await page.getByLabel('프롬프트 입력').fill('vLLM 프로젝트 최근 소식을 웹에서 찾아서 알려줘')
  await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/api/sessions') && r.request().method() === 'POST'),
    page.getByLabel('프롬프트 입력').press('Enter'),
  ])
  // The URL has to land as soon as the session exists, not when the answer is
  // done — a web-search turn runs for half a minute, and a reload before then
  // must not lose the conversation.
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 10_000 })

    // Wait for the turn to finish before looking for a step. Under queue
    // pressure the steps appear at unpredictable times, and a fixed wait races
    // them — the label is not missing, it has not arrived yet. Labels survive
    // completion (the timeline collapses to a summary but keeps each step's
    // name), so waiting costs nothing.
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 300_000 })

  // An unknown step `type` must not leave the icon component undefined — that
  // unmounts the whole tree. Match the step's own label: a plain /웹 검색/
  // ("web search") also matches the composer's toggle, so it would pass with
  // no step rendered at all.
  // 검색이 빨리 끝나면 '중' 은 접힌 요약('작업 완료 | 웹 검색 …')으로 바뀐
  // 뒤다 — 이 사례의 주장은 스텝이 화면을 죽이지 않는다는 것이지, 특정
  // 시제의 라벨을 잡아채는 것이 아니다.
  await expect(page.getByText(/웹 검색( 중)?/).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  // And the shell around the conversation, which is the tree a step-render
  // crash would have taken with it. Below 1024px the sidebar is a drawer, so
  // it has to be opened before it can be read.
  await openSidebar(page)
  await expect(page.getByRole('link', { name: '아티팩트' })).toBeVisible()

  await page.reload()
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('vLLM 프로젝트 최근 소식을 웹에서 찾아서 알려줘').first()).toBeVisible({
    timeout: 20_000,
  })

  expect(crashes, '렌더 예외').toEqual([])
})

test('대화 안에서 모델을 바꾸면 그 대화에 반영된다', async ({ page }) => {
  await page.goto('/new/chat')
  await pickLocal(page)
  await page.getByLabel('프롬프트 입력').fill('안녕')
  await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/api/sessions') && r.request().method() === 'POST'),
    page.getByLabel('프롬프트 입력').press('Enter'),
  ])
  // The URL has to land as soon as the session exists, not when the answer is
  // done — a web-search turn runs for half a minute, and a reload before then
  // must not lose the conversation.
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 10_000 })

  // Wait for the conversation itself to be on screen, not just its URL. Until
  // the row lands in the store the picker has no conversation to write to, and
  // a change made in that window went to the surface default instead.
  await expect(page.getByText('안녕').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 180_000 })

  // pickLocal 이 고르는 로컬은 3.5(122b)일 수도 3.6(35b)일 수도 있다 —
  // 카탈로그 사정이지 이 시험의 주장 대상이 아니다.
  const picker = page.getByRole('button', { name: /qwen3\.[56]/i }).first()
  await expect(picker).toBeVisible({ timeout: 60_000 })

  // Whatever else the catalogue offers, not a model named in this file. Which
  // models the proxy serves changes between runs, and a hardcoded id turned a
  // catalogue change into a failure of the feature under test.
  await picker.click()
  // Scope to the open menu. `page.getByRole('button')` alone also matches the
  // trigger and every sidebar row, and the entries are plain <button> inside a
  // role="menu" container (components/ui Dropdown) — not menuitem.
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible({ timeout: 10_000 })
  const other = menu
    .getByRole('button')
    .filter({ hasNotText: /Qwen3\.[56]/i })
    .filter({ hasText: /1k당 입력/ })
    .first()
  const label = (await other.innerText()).split('\n')[0].trim()

  await other.click()

    // The picker shows that conversation's model, and keeps showing it after
    // a reload.
  const shows = page.getByRole('button', { name: new RegExp(escapeRe(label)) }).first()
  await expect(shows).toBeVisible({ timeout: 20_000 })

  // Reload until the server agrees rather than waiting on the PATCH itself.
  // The write is fire-and-forget by design — the picker must not block on it —
  // so watching for the request made the test race the very behaviour it is
  // there to allow.
  await expect(async () => {
    await page.reload()
    await expect(shows).toBeVisible({ timeout: 10_000 })
  }).toPass({ timeout: 60_000 })
})

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

test('사용자마다 자기 LiteLLM 키가 있고, 관리자가 회전시킬 수 있다', async ({ page }) => {
  // Every user needs their own key, otherwise nothing downstream can tell one
  // person's spend from another's. The admin table is where that is visible,
  // and "전용 키 없음" ("no dedicated key") is the state that means attribution
  // is broken.
  await page.goto('/admin/users')
  // Search rather than scan: the table pages at forty rows. Matched on the
  // table's own placeholder — a bare `/검색/` also matches the sidebar's
  // conversation search.
  await page.getByPlaceholder('이름 또는 이메일').fill(E2E_ADMIN.email)

  const row = page.locator('tr', { hasText: E2E_ADMIN.email })
  await expect(row).toBeVisible({ timeout: 15_000 })

  // Read whatever is there first: an account that predates per-user keys
  // shows "전용 키 없음" until something mints one, and the button handles both
  // cases.
  const before = await row.locator('.font-mono').first().innerText().catch(() => '')
  await row.getByRole('button', { name: /LiteLLM 키 (재)?발급/ }).click()

  const preview = row.locator('.font-mono').first()
  await expect(preview).toBeVisible({ timeout: 20_000 })
  // Rotation is three round trips to the proxy — revoke, ensure user, issue —
  // so the row can still be showing the old preview well past the five seconds
  // an assertion waits by default.
  await expect(preview).not.toHaveText(before, { timeout: 30_000 })
  await expect(row.getByText('전용 키 없음')).toHaveCount(0)

  // A rotation the browser can read back would defeat the point of the key never
  // leaving the server.
  await expect(page.locator('body')).not.toContainText('sk-')
})

test('크레딧 한도를 바꾸면 LiteLLM 에 반영될 금액이 함께 보인다', async ({ page }) => {
  // The number the dialog quotes is served by the API, not computed in the
  // client — a screen promising "$6.00 will be applied" while the API applies
  // something else is worse than a screen that says nothing.
  await page.goto('/admin/users')
  // Filtered first: the table pages, so scanning for a row only works while the
  // instance has few enough accounts to fit on one page.
  await page.getByPlaceholder('이름 또는 이메일').fill(E2E_ADMIN.email)
  const row = page.locator('tr', { hasText: E2E_ADMIN.email })
  await expect(row).toBeVisible({ timeout: 15_000 })
  await row.getByRole('button', { name: '크레딧', exact: true }).click()

  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('월 크레딧').fill('500000')
  await expect(dialog.getByText(/LiteLLM 에도 \$6\.00 한도로 반영됩니다/)).toBeVisible()

  await dialog.getByRole('button', { name: '취소' }).click()
})

test('관리자가 계정을 삭제하면 목록에서 사라진다', async ({ page }) => {
  // Deletion has to exist, not just suspension — otherwise test accounts pile
  // up and sit in the approval queue forever.
  const email = `e2e-del-${Date.now().toString(36)}@example.com`
  await seedPendingUser(page, email)

  await page.goto('/admin/users')
  await page.getByPlaceholder('이름 또는 이메일').fill(email)
  const row = page.locator('tr', { hasText: email })
  await expect(row).toBeVisible({ timeout: 15_000 })
  await row.getByRole('button', { name: '계정 삭제' }).click()

  // Confirmed in a dialog, never on the button — this one does not come back.
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText(email)).toBeVisible()
  await dialog.getByRole('button', { name: '삭제', exact: true }).click()

  await expect(row).toHaveCount(0, { timeout: 20_000 })
  await page.reload()
  await page.getByPlaceholder('이름 또는 이메일').fill(email)
  await expect(page.locator('tr', { hasText: email })).toHaveCount(0, { timeout: 20_000 })
})

test('컴포저 메뉴가 화면 밖으로 나가지 않는다', async ({ page }) => {
  // Every menu on the composer row is anchored a few pixels above the bottom of
  // the window. They all opened downward with no height cap, so what you saw was
  // the header and nothing else — the model picker included.
  await page.goto('/new/chat')
  await page.waitForTimeout(1200)
  const height = page.viewportSize()!.height

  const opens: [string, () => Promise<void>][] = [
    ['모델', () =>
      page.getByRole('button').filter({ hasText: /qwen|glm|claude|gpt|gemini|grok/i }).last().click()],
    ['스킬', () => page.getByRole('button', { name: '스킬', exact: true }).first().click()],
    ['커넥터', () => page.getByRole('button', { name: '커넥터', exact: true }).first().click()],
    ['모델 비교', () => page.getByRole('button', { name: '모델 비교', exact: true }).first().click()],
  ]

  for (const [label, open] of opens) {
    await open()
    const menu = page.getByRole('menu').first()
    await expect(menu, label).toBeVisible({ timeout: 10_000 })
    const box = await menu.boundingBox()
    expect(box, label).not.toBeNull()
    expect(box!.y, `${label} 위쪽 잘림`).toBeGreaterThanOrEqual(-1)
    expect(box!.y + box!.height, `${label} 아래쪽 잘림`).toBeLessThanOrEqual(height + 1)
    await page.keyboard.press('Escape')
    await expect(menu).toHaveCount(0)
  }
})

test('고른 모델이 새로고침 후에도 남는다', async ({ page }) => {
  // The surface default lived only in memory, seeded with ids from the deleted
  // mock catalogue. Every reload threw the choice away and fell back to the
  // cheapest model — which reads as the picker doing nothing at all.
  await page.goto('/new/chat')
  await page.waitForTimeout(1200)
  const trigger = page.getByRole('button').filter({ hasText: /qwen|glm|claude|gpt|gemini|grok/i }).last()
  const before = (await trigger.innerText()).trim()

  await trigger.click()
  const other = page
    .getByRole('menu')
    .getByRole('button')
    .filter({ hasNotText: before })
    .first()
  const label = (await other.innerText()).split('\n')[0].trim()
  await other.click()

  await page.reload()
  await page.waitForTimeout(1500)
  await expect(
    page.getByRole('button').filter({ hasText: label }).last(),
  ).toBeVisible({ timeout: 20_000 })
})

test('환경설정 스위치가 계정에 저장되고 화면에 반영된다', async ({ page }) => {
  // Three switches with nothing behind them, under a line admitting they were
  // not saved. They are on the account now and each one does something.
  await page.goto('/settings/preferences')
  const usage = page.getByRole('switch', { name: '토큰·크레딧 표시' })
  await expect(usage).toBeVisible({ timeout: 15_000 })

  // Read the state rather than assuming it: this is an account-wide setting and
  // an earlier run that died before its cleanup would leave it either way.
  const was = await usage.getAttribute('aria-checked')
  if (was !== 'true') {
    await Promise.all([
      page.waitForResponse(
        (r) => r.url().endsWith('/api/auth/me') && r.request().method() === 'PATCH',
        { timeout: 30_000 },
      ),
      usage.click(),
    ])
  }

  // The switch writes in the background; reloading before the PATCH lands reads
  // back the old value.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().endsWith('/api/auth/me') && r.request().method() === 'PATCH',
      { timeout: 30_000 },
    ),
    usage.click(),
  ])
  await page.reload()
  await expect(page.getByRole('switch', { name: '토큰·크레딧 표시' })).toHaveAttribute(
    'aria-checked',
    'false',
    { timeout: 15_000 },
  )

  try {
    await page.goto('/new/chat')
    await pickLocal(page)
    await page.getByLabel('프롬프트 입력').fill('1 더하기 1은?')
    await page.getByLabel('프롬프트 입력').press('Enter')
    await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 15_000 })
    await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 120_000 })
    await expect(page.getByText(/in ·.*out ·/)).toHaveCount(0)
  } finally {
    // Instance-wide state again: leaving it off changes what every later test
    // sees on screen.
    await page.goto('/settings/preferences')
    await page
      .getByRole('switch', { name: '토큰·크레딧 표시' })
      .click({ timeout: 15_000 })
      .catch(() => {})
  }
})

test('보고서를 만들면 섹션이 채워지고 내보낼 수 있다', async ({ page }) => {
  test.setTimeout(600_000)
  await page.goto('/new/report')
  await page.getByLabel('프롬프트 입력').fill('전이학습이 소량 데이터에서 왜 효과적인지 짧은 기술 검토 보고서.')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })

  // The first pass plans and stops; nothing is written until it is approved, so
  // there is no panel and no denominator before this.
  await approvePlan(page, 480_000)

  // The outline lands before any section is written, so the panel has a real
  // denominator from the start. Asserting the *initial* 0/N would race the first
  // section finishing; what matters is that the count exists and then completes.
  // The count is on the button that opens the contents.
  //
  // It used to be a line inside a column that stood beside the document at
  // every width, and that column was 208px of the document's own room — so the
  // contents became a drawer and the count moved onto its handle. The line is
  // still there, inside the closed drawer, which is why looking for it by text
  // finds an element and calls it hidden.
  const counter = page.getByRole('button', { name: /목차 \d+\/[3-8]/ })
  await expect(counter).toBeVisible({ timeout: 180_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 480_000 })

  const progress = await counter.innerText()
  const [done, total] = progress.match(/(\d+)\/(\d+)/)!.slice(1).map(Number)
  expect(done, '작성되지 않은 섹션').toBe(total)

  await page.getByRole('button', { name: '내보내기', exact: true }).click()
  await expect(page.getByRole('menuitem', { name: 'Word 문서' })).toBeVisible()
  const download = page.waitForEvent('download', { timeout: 60_000 })
  await page.getByRole('menuitem', { name: 'Word 문서' }).click()
  expect((await download).suggestedFilename()).toMatch(/\.docx$/)
})

test('그림·클립 화면에는 보낼 곳 없는 첨부·스킬 버튼이 없다', async ({ page }) => {
  // Neither control may render here. `generateImages`/`generateAudio`/
  // `generateVideo` send the prompt and the option chips, and the endpoints
  // behind them have no field an upload or a skill could ride in — so an
  // attachment offered on these surfaces is silently dropped at submit, after
  // 12,000–32,000 크레딧 and several minutes.
  await page.goto('/new/chat')
  await expect(page.getByRole('button', { name: '첨부' }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '스킬', exact: true }).first()).toBeVisible()

  for (const surface of ['image', 'av'] as const) {
    // Only where the workspace has the surface on. Both default to off — they
    // spend credits per generation — and a screen that says so has no composer
    // for these controls to be absent from.
    if (!(await surfaceOn(page, surface))) continue
    await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
    await expect(page.getByRole('button', { name: '첨부' })).toHaveCount(0)
    // The picker itself, not only its button: a hidden input is still reachable.
    await expect(page.locator('input[type="file"]')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '스킬', exact: true })).toHaveCount(0)
    // Web search was already held to this rule; these two now read the same.
    await expect(page.getByRole('button', { name: '웹 검색' })).toHaveCount(0)
  }
})

test('한국어 굵은 글씨가 별표로 새지 않는다', async ({ page }) => {
  // CommonMark will not close `**` between a bracket and a Korean particle, which
  // is the exact shape every Korean answer takes.
  await page.goto('/new/chat')
  await pickLocal(page)
  await page.getByLabel('프롬프트 입력').fill(
    '다음 문장을 그대로 한 번만 출력해줘. 다른 말은 하지 마: 첫째, **지식의 전이(Knowledge Transfer)**이다.',
  )
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 15_000 })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 120_000 })

  await expect(page.getByText('지식의 전이').first()).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('article, .prose').getByText('**')).toHaveCount(0)
})
