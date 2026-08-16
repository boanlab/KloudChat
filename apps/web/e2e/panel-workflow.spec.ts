import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The report and deck panels as working surfaces.
 *
 * Everything here runs against artifacts posted straight to the API, so no
 * model is called: what is under test is the panel — the rail, the stage, the
 * selection handle, presenter view — not the writing that fills it. A spec that
 * had to generate a deck first would cost minutes and credits to assert that a
 * thumbnail is clickable.
 */

const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, {
    ...(init || {}),
    headers: { ...((init || {}).headers || {}), Authorization: 'Bearer ' + accessToken },
  })
  if (!r.ok || r.status === 204) return null
  return await r.json()
}`

function post(page: Page, payload: unknown) {
  return page.evaluate(
    async ([fn, body]) =>
      await eval(fn as string)('/api/artifacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    [AS_USER, payload] as const,
  )
}

function remove(page: Page, id: string) {
  return page.evaluate(
    async ([fn, artifactId]) =>
      await eval(fn as string)(`/api/artifacts/${artifactId}`, { method: 'DELETE' }),
    [AS_USER, id] as const,
  )
}

/** Opens an artifact by title from the gallery and returns its dialog. */
async function openPreview(page: Page, title: string) {
  await page.goto('/artifacts')
  // The card's own thumbnail is what opens it; the title beside it is not a
  // control. `.last()` is the innermost div that holds both — the card.
  const card = page
    .locator('div')
    .filter({ has: page.getByText(title, { exact: true }) })
    .filter({ has: page.locator('button.aspect-video') })
    .last()
  await card.locator('button.aspect-video').first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible({ timeout: 15_000 })
  return dialog
}

const SLIDES = [
  { id: 's1', layout: 'title', title: '가상환경 관리', body: '연구실 신입생 대상', accent: '#5b5bd6' },
  { id: 's2', layout: 'bullets', title: '왜 격리하는가', bullets: ['의존성 충돌', '재현 가능성'], notes: '여기서 사례를 든다' },
  { id: 's3', layout: 'bullets', title: 'venv 로 시작하기', bullets: ['python -m venv .venv', 'source .venv/bin/activate'] },
  { id: 's4', layout: 'quote', title: '요약', body: '환경은 코드의 일부다' },
]

test.describe('슬라이드 패널', () => {
  let deckId = ''
  const title = `패널 확인 덱 ${Date.now().toString(36)}`

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage()
    await signIn(page)
    const deck = await post(page, {
      kind: 'deck',
      title,
      data: { kind: 'deck', theme: '기본', slides: SLIDES },
    })
    deckId = (deck as { id: string }).id
    await page.close()
  })

  test.afterAll(async ({ browser }) => {
    if (!deckId) return
    const page = await browser.newPage()
    await signIn(page)
    await remove(page, deckId)
    await page.close()
  })

  test('레일이 모든 장을 담고, 고른 장이 무대에 선다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)

    // One thumbnail per slide, and the deck's own count beside the title.
    await expect(panel.locator('button.aspect-video')).toHaveCount(SLIDES.length)
    await expect(panel.getByText(`${SLIDES.length}장`)).toBeVisible()

    // The stage follows the rail. Slide 3's bullet is the proof it is the one
    // being drawn large, not just highlighted in the list.
    await panel.getByRole('button', { name: '3번 장' }).click()
    await expect(panel.getByText('python -m venv .venv').first()).toBeVisible()

    // Outline view answers the other question — the order of the argument.
    await panel.getByRole('button', { name: '차례로' }).click()
    await expect(panel.getByText('왜 격리하는가').first()).toBeVisible()
    await expect(panel.getByText('요약').first()).toBeVisible()
    await panel.getByRole('button', { name: '그림으로' }).click()
    await expect(panel.locator('button.aspect-video')).toHaveCount(SLIDES.length)
  })

  test('앞뒤 화살표로 장을 넘긴다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)

    // On the first slide there is nothing before it.
    await expect(panel.getByRole('button', { name: '이전 장' })).toBeDisabled()
    await panel.getByRole('button', { name: '다음 장' }).click()
    await expect(panel.getByText('의존성 충돌').first()).toBeVisible()
    // The notes travel with the slide.
    await expect(panel.getByText('여기서 사례를 든다')).toBeVisible()
    await panel.getByRole('button', { name: '이전 장' }).click()
    await expect(panel.getByText('연구실 신입생 대상').first()).toBeVisible()
  })

  test('발표 모드는 노트를 달고 키보드로 넘어간다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('button', { name: '발표' }).click()

    const stage = page.getByRole('dialog', { name: '발표 모드' })
    await expect(stage).toBeVisible()
    await expect(stage.getByText(`1 / ${SLIDES.length}`)).toBeVisible()

    await page.keyboard.press('ArrowRight')
    await expect(stage.getByText(`2 / ${SLIDES.length}`)).toBeVisible()
    // What the presenter reads, on the screen only they see.
    await expect(stage.getByText('여기서 사례를 든다')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(stage).toBeHidden()
    // Escape ends the presentation, not the panel behind it.
    await expect(panel.getByRole('button', { name: '발표' })).toBeVisible()
  })

  test('편집과 팩트체크는 패널을 열자마자 손에 닿는다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await expect(panel.getByRole('button', { name: '팩트체크' })).toBeVisible()
    await expect(panel.getByRole('button', { name: '텍스트 수정' })).toBeVisible()
    await expect(panel.getByRole('button', { name: '내보내기', exact: true })).toBeEnabled()
  })
})

/**
 * The work log, live and then settled.
 *
 * Steps are not stored on the message, so the only place this card exists is
 * during a run — which is also the only moment its "how much is left" figure
 * means anything. That figure comes from the plan the outline pass already
 * has, so it has to be read off a real generation rather than a fixture.
 */
test('작업 단계 카드는 남은 개수를 세다 접힌다', async ({ page }) => {
  test.setTimeout(420_000)
  await signIn(page)

  await page.goto('/new/slides')
  await page.getByLabel('프롬프트 입력').fill('파이썬 가상환경을 설명하는 짧은 발표 슬라이드')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })

  // Open while it runs, and counting down against the outline's own total —
  // not against the steps that happen to have arrived.
  const running = page.getByRole('button', { name: /작업 중/ })
  await expect(running).toBeVisible({ timeout: 180_000 })
  await expect(running).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText(/\d+개 남음/)).toBeVisible({ timeout: 180_000 })

  // Finished work stays on screen, struck through.
  await expect(page.locator('.line-through').first()).toBeVisible({ timeout: 180_000 })

  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 360_000 })
  const done = page.getByRole('button', { name: /작업 완료|중단됨/ }).first()
  await expect(done).toBeVisible()
  // Settled, so it is a one-line summary until asked otherwise.
  await expect(done).toHaveAttribute('aria-expanded', 'false')
  await done.click()
  await expect(done).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText(/\d+단계/).first()).toBeVisible()
})

/**
 * Opening and closing the artifact, from the conversation that made it.
 *
 * The loop has to be closed in both directions: a panel that cannot be put
 * away crowds the transcript out of a laptop screen, and one that cannot be
 * brought back makes closing it a decision nobody wants to make.
 */
test('아티팩트는 대화에서 열고, 닫고, 다시 열 수 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const session = await page.evaluate(async (fn) => {
    const rows = await eval(fn)('/api/sessions')
    const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
    return list.find((s: { artifactId: string | null }) => s.artifactId) ?? null
  }, AS_USER)
  test.skip(!session, '결과물이 붙은 대화가 아직 없습니다.')

  await page.goto(`/s/${session.id}`)
  // The result is the point of the conversation, so the panel arrives open.
  // `aside` is also the sidebar; the artifact panel is the one carrying the
  // close button.
  const open = page.getByRole('button', { name: /열기$/ })
  const panel = page
    .locator('aside')
    .filter({ has: page.getByRole('button', { name: '닫기' }) })
  await expect(panel).toBeVisible({ timeout: 20_000 })

  // Room to read, and back again.
  const wider = panel.getByRole('button', { name: '넓게 보기' })
  await expect(wider).toBeVisible()
  await wider.click()
  const narrower = panel.getByRole('button', { name: '패널 좁히기' })
  await expect(narrower).toBeVisible()
  await narrower.click()
  await expect(wider).toBeVisible()

  // Put it away — the transcript gets the whole window back.
  await panel.getByRole('button', { name: '닫기' }).click()
  await expect(panel).toHaveCount(0)

  // …and closing is only safe because this brings it back. Before the session
  // loaded its own artifacts, arriving by URL left this button off the header
  // entirely and the result was unreachable from the conversation that made it.
  await expect(open).toBeVisible()
  await open.click()
  await expect(panel).toBeVisible()
  await expect(open).toHaveCount(0)
})

const SECTIONS = [
  {
    id: 'r1',
    heading: '배경',
    level: 1,
    status: 'done',
    content: '전이학습은 적은 표본에서도 쓸 만한 표현을 빌려 온다.',
  },
  {
    id: 'r2',
    heading: '한계',
    level: 1,
    status: 'done',
    content: '도메인이 멀어질수록 빌려 온 표현은 빠르게 쓸모를 잃는다.',
  },
]

test.describe('보고서 패널', () => {
  let reportId = ''
  const title = `패널 확인 보고서 ${Date.now().toString(36)}`

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage()
    await signIn(page)
    const report = await post(page, {
      kind: 'report',
      title,
      data: {
        kind: 'report',
        sections: SECTIONS,
        sources: [],
        citationStyle: 'APA',
        wordCount: 120,
      },
    })
    reportId = (report as { id: string }).id
    await page.close()
  })

  test.afterAll(async ({ browser }) => {
    if (!reportId) return
    const page = await browser.newPage()
    await signIn(page)
    await remove(page, reportId)
    await page.close()
  })

  test('저장 시점과 목차 진행이 머리말에 함께 보인다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    // Version history is reachable by the same name the restore flow uses.
    await expect(panel.getByRole('button', { name: '버전 기록' })).toBeVisible()
    await expect(panel.getByText('저장 시점 v1')).toBeVisible()
    await expect(panel.getByText(/\d+\/2 섹션/)).toBeVisible()
  })

  test('본문을 긁으면 그 대목을 고칠 손잡이가 나온다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)

    // Select the sentence the way a reader would — by dragging across it.
    const sentence = panel.getByText('도메인이 멀어질수록', { exact: false }).first()
    await expect(sentence).toBeVisible()
    // Measured in a retry loop, and the box that answered is the one used. The
    // panel refetches its artifact just after opening, so the node this
    // resolves to can be replaced between "is it visible" and "where is it" —
    // and `boundingBox()` returns null for a node no longer in the document.
    let box: { x: number; y: number; width: number; height: number } | null = null
    await expect
      .poll(async () => {
        box = await sentence.boundingBox().catch(() => null)
        return box !== null && box.width > 0
      }, { timeout: 15_000 })
      .toBe(true)
    const at = box!
    await page.mouse.move(at.x + 4, at.y + at.height / 2)
    await page.mouse.down()
    await page.mouse.move(at.x + at.width - 4, at.y + at.height / 2, { steps: 12 })
    await page.mouse.up()

    const handle = panel.getByRole('button', { name: '이 부분 고치기' })
    await expect(handle).toBeVisible({ timeout: 10_000 })
    await handle.click()

    // The instruction box opens on that section, carrying the passage it is
    // about — the reader never has to describe the sentence again.
    const note = panel.getByLabel('다시 쓰기 지시')
    await expect(note).toBeVisible()
    await expect(panel.getByLabel('고칠 대목')).toContainText('도메인이')
    await expect(panel.getByRole('button', { name: '선택 해제' })).toBeVisible()

    // Dropping the quotation leaves the instruction box open, not the reverse.
    await panel.getByRole('button', { name: '선택 해제' }).click()
    await expect(panel.getByRole('button', { name: '선택 해제' })).toHaveCount(0)
    await expect(note).toBeVisible()
  })

  test('넓게 보기는 다시 눌러 되돌린다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('button', { name: '넓게 보기' }).click()
    await expect(panel.getByRole('button', { name: '패널 좁히기' })).toBeVisible()
    await panel.getByRole('button', { name: '패널 좁히기' }).click()
    await expect(panel.getByRole('button', { name: '넓게 보기' })).toBeVisible()
  })
})
