import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { approveOnce, signIn } from './helpers'

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
    for (const heading of await panel.locator(`[title="${title}"]`).all()) {
      expect((await heading.boundingBox())?.height).toBeLessThanOrEqual(24)
      await expect(heading).toHaveCSS('white-space', 'nowrap')
    }
    // The rail must be the same slide at a smaller scale, not a large canvas
    // cropped into a thumbnail. Compare the title size relative to each
    // surface's width; the ratio stays constant when both use one renderer.
    const thumb = panel.locator('nav button.aspect-video').first()
    const stage = panel.locator('div.aspect-video').filter({ hasText: SLIDES[0].title }).last()
    const [thumbMetric, stageMetric] = await Promise.all([
      thumb.evaluate((node) => ({ width: node.getBoundingClientRect().width, font: Number.parseFloat(getComputedStyle(node.querySelector('h3')!).fontSize) })),
      stage.evaluate((node) => ({ width: node.getBoundingClientRect().width, font: Number.parseFloat(getComputedStyle(node.querySelector('h3')!).fontSize) })),
    ])
    expect(Math.abs(thumbMetric.font / thumbMetric.width - stageMetric.font / stageMetric.width)).toBeLessThan(0.015)
    // One thumbnail per slide, and where you are in them.
    await expect(panel.locator('button.aspect-video')).toHaveCount(SLIDES.length)
    // 보기 held a count badge — a number nobody could press, next to a list
    // already showing every slide. The tab holds the list toggle now, and that
    // says the same count as part of saying where you are.
    await panel.getByRole('tab', { name: '보기' }).click()
    await expect(
      panel.getByRole('button', { name: '장 목록' }),
    ).toContainText(`/${SLIDES.length}`)

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

  test('320px 폭에서도 머리말과 편집 버튼이 눌리지 않는다', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 720 })
    await signIn(page)
    const panel = await openPreview(page, title)

    const menu = panel.getByRole('tablist', { name: '슬라이드 메뉴' })
    for (const tab of ['홈', '삽입', '검토', '보기', '슬라이드 쇼', '파일']) await expect(menu.getByRole('tab', { name: tab })).toBeVisible()
    await menu.getByRole('tab', { name: '홈' }).focus()
    await page.keyboard.press('End')
    const fileTab = menu.getByRole('tab', { name: '파일' })
    await expect(fileTab).toHaveAttribute('aria-selected', 'true')
    const fileTabBox = await fileTab.boundingBox()
    expect(fileTabBox?.x).toBeGreaterThanOrEqual(0)
    expect((fileTabBox?.x ?? 0) + (fileTabBox?.width ?? 0)).toBeLessThanOrEqual(320)
    for (const [tab, name] of [['홈', '편집형'], ['검토', '검토 메모'], ['보기', '장 목록'], ['슬라이드 쇼', '발표'], ['파일', '내보내기']] as const) {
      await menu.getByRole('tab', { name: tab }).click()
      const button = panel.getByRole('button', { name }).first()
      await expect(button).toBeVisible()
      const box = await button.boundingBox()
      expect(box?.height).toBeGreaterThanOrEqual(31.5)
      expect(box?.width).toBeGreaterThanOrEqual(31.5)
    }

    await menu.getByRole('tab', { name: '홈' }).click()
    await panel.getByRole('button', { name: '덱 색 고르기' }).click()
    const designMenu = page.getByRole('menu')
    await expect(designMenu).toBeVisible()
    const designMenuBox = await designMenu.boundingBox()
    expect(designMenuBox?.x).toBeGreaterThanOrEqual(0)
    expect((designMenuBox?.x ?? 0) + (designMenuBox?.width ?? 0)).toBeLessThanOrEqual(320)
    await page.keyboard.press('Escape')
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '편집 도구' }).click()
    const toolbar = panel.getByLabel('슬라이드 편집 도구')
    await expect(toolbar).toBeVisible()
    await expect(panel.getByLabel('빠른 도구').getByRole('button', { name: '저장' })).toBeVisible()
    await expect(panel.getByLabel('빠른 도구').getByRole('button', { name: '편집 취소' })).toBeVisible()
    for (const button of await panel.getByLabel('빠른 도구').getByRole('button').all()) {
      expect((await button.boundingBox())?.height).toBeGreaterThanOrEqual(39.5)
    }
    await expect(panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '이 장 다시 만들기' })).toHaveCount(0)
    // 도구는 리본의 편집 칸 안에 있고, 넘치면 리본 줄이 스크롤한다 — 도구
    // 자체가 아니라. 화면을 넘지 않아야 하는 것은 그 줄이다.
    const ribbonRow = panel.getByRole('tabpanel', { name: '편집' })
    expect(await ribbonRow.evaluate((node) => node.clientWidth)).toBeLessThanOrEqual(320)
    await expect(ribbonRow.getByLabel('슬라이드 편집 도구')).toBeVisible()
    await expect(toolbar.getByRole('button', { name: '슬라이드 편집 실행 취소' })).toHaveCSS('flex-shrink', '0')
  })

  test('발표 모드는 노트를 달고 키보드로 넘어간다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('tab', { name: '슬라이드 쇼' }).click()
    await panel.getByRole('toolbar', { name: '슬라이드 쇼' }).getByRole('button', { name: '발표' }).click()

    const stage = page.getByRole('dialog', { name: '발표 모드' })
    await expect(stage).toBeVisible()
    await expect(stage.getByText(`1 / ${SLIDES.length}`)).toBeVisible()
    await expect(stage.getByRole('button', { name: '발표 시간 다시 시작' })).toHaveText(/^00:0\d$/)
    await expect(stage.getByRole('button', { name: '전체 화면' })).toHaveAttribute('aria-pressed', 'false')

    await page.keyboard.press('ArrowRight')
    await expect(stage.getByText(`2 / ${SLIDES.length}`)).toBeVisible()
    // What the presenter reads, on the screen only they see.
    await expect(stage.getByText('여기서 사례를 든다')).toBeVisible()

    // The rehearsal clock can be restarted without leaving the current slide.
    await stage.getByRole('button', { name: '발표 시간 다시 시작' }).click()
    await expect(stage.getByRole('button', { name: '발표 시간 다시 시작' })).toHaveText('00:00')

    await page.keyboard.press('Escape')
    await expect(stage).toBeHidden()
    // Escape ends the presentation, not the panel behind it.
    await expect(panel.getByRole('toolbar', { name: '슬라이드 쇼' }).getByRole('button', { name: '발표' })).toBeVisible()
  })

  test('장별 검토 메모를 저장하고 해결 상태로 관리한다', async ({ page }) => {
    await signIn(page)
    let panel = await openPreview(page, title)
    await panel.getByRole('tab', { name: '검토' }).click()
    await panel.getByRole('button', { name: '검토 메모' }).click()
    const review = panel.locator('aside[aria-label="검토 메모"]')
    await expect(review).toBeVisible()
    await expect(review.getByText('1번 장에 메모')).toBeVisible()

    await review.getByLabel('메모 내용').fill('표지의 대상 독자를 더 크게 보여 주세요.')
    const saved = page.waitForResponse((response) =>
      response.url().includes(`/api/artifacts/${deckId}`) && response.request().method() === 'PATCH',
    )
    await review.getByRole('button', { name: '메모 추가' }).click()
    await saved
    await expect(review.getByText('표지의 대상 독자를 더 크게 보여 주세요.')).toBeVisible()
    await expect(panel.getByLabel('미해결 메모 1개')).toBeVisible()

    await review.getByRole('button', { name: '검토 메모 닫기' }).click()
    await panel.getByRole('tab', { name: '파일' }).click()
    await panel.getByRole('button', { name: '내보내기', exact: true }).click()
    await expect(page.getByText(/내보내기 전 확인 \d+건/)).toBeVisible()
    await page.getByRole('menuitem', { name: '미해결 검토 메모 1개' }).click()
    await expect(review).toBeVisible()

    // It is artifact data, not drawer-local state.
    await page.reload()
    panel = await openPreview(page, title)
    await panel.getByRole('tab', { name: '검토' }).click()
    await panel.getByRole('button', { name: '검토 메모' }).click()
    const restored = panel.locator('aside[aria-label="검토 메모"]')
    await expect(restored.getByText('표지의 대상 독자를 더 크게 보여 주세요.')).toBeVisible()

    const resolved = page.waitForResponse((response) =>
      response.url().includes(`/api/artifacts/${deckId}`) && response.request().method() === 'PATCH',
    )
    await restored.getByRole('button', { name: '해결로 표시' }).click()
    await resolved
    await expect(restored.getByRole('button', { name: '다시 열기' })).toBeVisible()

    // A later slide edit must not replace the whole deck payload and erase
    // review metadata that is not part of the visible slide canvas.
    await restored.getByRole('button', { name: '검토 메모 닫기' }).click()
    // 메모는 검토 탭에서 열었으므로 리본은 아직 거기에 있다. 편집 도구는 홈에.
    await panel.getByRole('tab', { name: '홈' }).click()
    await panel.getByRole('button', { name: '편집 도구' }).click()
    await panel.getByLabel('슬라이드 텍스트').fill('가상환경 관리\n연구실 신입생 대상\n검토 뒤에도 남는 본문')
    await panel.getByRole('button', { name: '저장', exact: true }).click()
    await expect(panel.getByLabel('슬라이드 텍스트')).toHaveCount(0)

    await page.reload()
    panel = await openPreview(page, title)
    await panel.getByRole('tab', { name: '검토' }).click()
    await panel.getByRole('button', { name: '검토 메모' }).click()
    await expect(panel.getByText('표지의 대상 독자를 더 크게 보여 주세요.')).toBeVisible()
    await expect(panel.getByRole('button', { name: '다시 열기' })).toBeVisible()

    const deleted = page.waitForResponse((response) =>
      response.url().includes(`/api/artifacts/${deckId}`) && response.request().method() === 'PATCH',
    )
    await panel.getByRole('button', { name: '메모 삭제' }).click()
    await deleted
    await expect(panel.getByText('표지의 대상 독자를 더 크게 보여 주세요.')).toHaveCount(0)
  })

  test('편집·팩트체크·내보내기는 메뉴에서 바로 찾는다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await expect(panel.getByRole('button', { name: '팩트체크' })).toBeVisible()
    await expect(panel.getByRole('button', { name: '편집 도구' })).toBeVisible()
    await panel.getByRole('tab', { name: '파일' }).click()
    await expect(panel.getByRole('button', { name: '내보내기', exact: true })).toBeEnabled()
  })

  test('파일 메뉴에서 PPTX와 PDF를 실제로 내려받는다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('tab', { name: '파일' }).click()
    for (const [label, extension, magic] of [
      ['PowerPoint', 'pptx', 'PK'],
      ['PDF (발표용)', 'pdf', '%PDF'],
    ] as const) {
      await panel.getByRole('button', { name: '내보내기', exact: true }).click()
      const download = page.waitForEvent('download', { timeout: 90_000 })
      await page.getByRole('menuitem', { name: label }).click()
      const file = await download
      expect(file.suggestedFilename()).toMatch(new RegExp(`\\.${extension}$`, 'i'))
      const path = await file.path()
      expect(path).toBeTruthy()
      const bytes = await readFile(path!)
      expect(bytes.subarray(0, magic.length).toString()).toBe(magic)
      expect(bytes.length).toBeGreaterThan(2_000)
    }
  })

  test('편집 도구는 홈 리본에 한 번만 나타난다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await expect(panel.getByRole('button', { name: '편집 도구' })).toHaveCount(1)
    await expect(panel.getByLabel('즐겨찾기')).toHaveCount(0)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '편집 도구' }).click()
    await expect(panel.getByLabel('슬라이드 편집 도구')).toBeVisible()
  })

  test('슬라이드 변경을 저장하지 않고 닫으려면 먼저 확인한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '편집 도구' }).click()
    await panel.getByLabel('슬라이드 텍스트').fill('아직 저장하지 않은 슬라이드 본문')
    await panel.getByLabel('발표 노트').fill('아직 저장하지 않은 발표 노트')
    await panel.getByLabel('빠른 도구').getByRole('button', { name: '닫기' }).click()
    const confirm = page.getByRole('dialog', { name: '저장하지 않은 변경 내용이 있습니다' })
    await expect(confirm).toBeVisible()
    await confirm.getByRole('button', { name: '취소' }).click()
    await expect(panel).toBeVisible()
    await panel.getByLabel('빠른 도구').getByRole('button', { name: '닫기' }).click()
    await confirm.getByRole('button', { name: '저장하지 않고 닫기' }).click()
    await expect(panel).toHaveCount(0)
  })

  test('슬라이드 편집 중 다른 저장을 덮어쓰지 않고 최신본으로 복구한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '편집 도구' }).click()
    await panel.getByLabel('슬라이드 텍스트').fill('로컬 제목\n로컬 편집 내용')

    await page.evaluate(async ([fn, id]) => {
      const artifact = await eval(fn as string)(`/api/artifacts/${id}`)
      const data = artifact.data
      data.slides[0].body = '외부 편집 보존'
      await eval(fn as string)(`/api/artifacts/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data, expectedVersion: artifact.version }),
      })
    }, [AS_USER, deckId] as const)

    await panel.getByLabel('빠른 도구').getByRole('button', { name: '저장' }).click()
    await expect(panel.getByText(/다른 곳에서 이미 수정/)).toBeVisible()
    await expect(panel.getByLabel('슬라이드 텍스트')).toHaveValue(/로컬 편집 내용/)
    await expect(panel.getByRole('button', { name: '내 편집 내용 복사' })).toBeVisible()

    const stored = await page.evaluate(async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`), [AS_USER, deckId] as const)
    expect((stored as { data: { slides: Array<{ body?: string }> } }).data.slides[0].body).toBe('외부 편집 보존')

    await panel.getByRole('button', { name: '최신본 불러오기' }).click()
    await expect(panel.getByText(/다른 곳에서 이미 수정/)).toHaveCount(0)
    await expect(panel.getByLabel('슬라이드 텍스트')).toHaveValue(/외부 편집 보존/)
    await expect(panel.getByLabel('슬라이드 텍스트')).not.toHaveValue(/로컬 편집 내용/)
  })

  test('Ctrl+S로 슬라이드 편집을 저장한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '편집 도구' }).click()
    const source = panel.getByLabel('슬라이드 텍스트')
    await source.fill('키보드 저장\nCtrl+S로 저장한 슬라이드')
    expect(await page.evaluate(() => window.dispatchEvent(new Event('beforeunload', { cancelable: true })))).toBe(false)
    const saved = page.waitForResponse((response) => response.url().includes(`/api/artifacts/${deckId}`) && response.request().method() === 'PATCH')
    await source.press('Control+s')
    await saved
    expect(await page.evaluate(() => window.dispatchEvent(new Event('beforeunload', { cancelable: true })))).toBe(true)
    await expect(source).toHaveCount(0)
    await expect(panel.getByText('Ctrl+S로 저장한 슬라이드').first()).toBeVisible()
  })

  test('이전 슬라이드를 확인 후 복원하고 바로 다시 편집한다', async ({ page }) => {
    await signIn(page)
    const before = await page.evaluate(async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`), [AS_USER, deckId] as const) as { version: number; data: { slides: typeof SLIDES } }
    const changed = structuredClone(before.data)
    changed.slides[0].body = '복원하면 사라질 슬라이드 문장'
    ;[changed.slides[0], changed.slides[1]] = [changed.slides[1], changed.slides[0]]
    await page.evaluate(async ([fn, id, data, version]) => await eval(fn as string)(`/api/artifacts/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data, expectedVersion: version }),
    }), [AS_USER, deckId, changed, before.version] as const)

    const panel = await openPreview(page, title)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '편집 도구' }).click()
    await panel.getByLabel('슬라이드 텍스트').fill('저장하지 않은 슬라이드 편집')
    await panel.getByRole('tab', { name: '검토' }).click()
    await panel.getByRole('button', { name: /버전 기록/ }).click()
    const history = page.getByRole('dialog', { name: '버전 기록' })
    await history.getByRole('button', { name: `v${before.version} 내용 보기` }).click()
    await expect(history.getByLabel(`v${before.version} 내용 미리보기`)).toContainText(before.data.slides[0].body ?? before.data.slides[0].title)
    await expect(history.getByLabel('현재 판과 변경 비교')).toContainText('수정 1장')
    await expect(history.getByLabel('현재 판과 변경 비교')).toContainText('이동 2장')
    await history.getByRole('button', { name: `v${before.version} 로 되돌리기` }).click()
    const confirm = page.getByRole('dialog', { name: `v${before.version}으로 되돌릴까요?` })
    await expect(confirm).toContainText('저장하지 않은 변경 내용은 사라집니다')
    await confirm.getByRole('button', { name: '이 버전으로 복원' }).click()
    await expect(panel.getByLabel('슬라이드 텍스트')).toHaveCount(0)
    await expect(panel.getByText('복원하면 사라질 슬라이드 문장')).toHaveCount(0)

    await panel.getByRole('tab', { name: '홈' }).click()
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '편집 도구' }).click()
    await panel.getByLabel('슬라이드 텍스트').fill('복원 뒤 재저장\n정상 저장 확인')
    await panel.getByLabel('빠른 도구').getByRole('button', { name: '저장' }).click()
    await expect(panel.getByLabel('슬라이드 텍스트')).toHaveCount(0)
  })

  test('같은 덱을 세 가지 디자인 계열로 바꾸고 저장한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)

    const captures: Buffer[] = []
    // 셋뿐인 선택은 리본에 그대로 서 있다 — 드롭다운을 열면 고른 것 하나만
    // 보이고 나머지는 눌러 봐야 알았다.
    for (const [buttonName, storedStyle] of [
      ['포스터형', 'poster'],
      ['미니멀', 'minimal'],
      ['편집형', 'editorial'],
    ] as const) {
      const saved = page.waitForResponse((response) =>
        response.url().includes(`/api/artifacts/${deckId}`) && response.request().method() === 'PATCH',
      )
      await panel.getByRole('button', { name: buttonName, exact: true }).click()
      await saved
      await expect(
        panel.getByRole('button', { name: buttonName, exact: true }),
      ).toHaveAttribute('aria-pressed', 'true')
      const artifact = await page.evaluate(
        async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`),
        [AS_USER, deckId] as const,
      )
      expect((artifact as { data: { design: { visualStyle: string } } }).data.design.visualStyle).toBe(storedStyle)
      captures.push(await panel.screenshot())
    }
    expect(captures[0].equals(captures[1])).toBe(false)
    expect(captures[1].equals(captures[2])).toBe(false)

    await panel.getByRole('button', { name: '덱 색 고르기' }).click()
    const recoloured = page.waitForResponse((response) => response.url().includes(`/api/artifacts/${deckId}`) && response.request().method() === 'PATCH')
    await page.getByRole('menuitemcheckbox', { name: '청록', exact: true }).click()
    await recoloured
    const recolouredDeck = await page.evaluate(async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`), [AS_USER, deckId] as const)
    expect((recolouredDeck as { data: { design: { accent: string; visualStyle: string } } }).data.design).toMatchObject({ accent: '#0f766e', visualStyle: 'editorial' })
  })
})

/**
 * A deck that came out of a 서식, on the same panel as the JSON one.
 *
 * Picking a shape turns a slides session into one HTML document, and for as
 * long as that document could only be previewed and read as source, choosing
 * the better-looking deck cost the ability to show it to anybody. The markup
 * here is what `design_templates.assemble` writes — one `<section class=
 * "slide">` per slide inside a seed that carries its own stylesheet — so what
 * this walks is the panel, not the writing.
 */
const PAGE_DECK = `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>학과 서버 교체</title>
<style>
  body { margin: 0; scroll-snap-type: y mandatory; font-family: sans-serif; }
  .slide { scroll-snap-align: start; min-height: 100vh; padding: 7vh 8vw; border-left: 6px solid #5b5bd6; }
  .slide h2 { font-size: 2.4rem; letter-spacing: -0.02em; }
</style>
</head>
<body>
<section class="slide cover"><h2>학과 서버 교체</h2><p class="lead">2026년 상반기 계획</p><span class="num">1</span></section>
<section class="slide"><h2>지금의 문제</h2><ul><li>디스크가 매주 찬다</li><li>야간 배치가 밀린다</li></ul><span class="num">2</span></section>
<section class="slide quote"><h2>제안</h2><blockquote>고칠 것은 장비가 아니라 주기다</blockquote><span class="num">3</span></section>
</body>
</html>`

const PAGE_DOC = `<!doctype html>
<html lang="ko">
<head><meta charset="utf-8" /><title>한 장 요약</title></head>
<body>
<div class="cover"><h1>한 장 요약</h1></div>
<section><h2>결정할 것</h2><p>교체 주기를 3년으로 줄일지.</p></section>
</body>
</html>`

test.describe('서식으로 만든 덱', () => {
  let deckId = ''
  let docId = ''
  const title = `패널 확인 서식 덱 ${Date.now().toString(36)}`
  const docTitle = `패널 확인 서식 문서 ${Date.now().toString(36)}`

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage()
    await signIn(page)
    const deck = await post(page, {
      kind: 'html',
      title,
      data: { kind: 'html', content: PAGE_DECK, templateId: 'deck-editorial', blocks: [] },
    })
    deckId = (deck as { id: string }).id
    const doc = await post(page, {
      kind: 'html',
      title: docTitle,
      data: { kind: 'html', content: PAGE_DOC, templateId: 'doc-brief', blocks: [] },
    })
    docId = (doc as { id: string }).id
    await page.close()
  })

  test.afterAll(async ({ browser }) => {
    const page = await browser.newPage()
    await signIn(page)
    if (deckId) await remove(page, deckId)
    if (docId) await remove(page, docId)
    await page.close()
  })

  test('덱은 서식으로 만들었어도 발표할 수 있다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await expect(panel.getByRole('button', { name: '미리보기' })).toBeVisible()

    await panel.getByRole('button', { name: '발표' }).click()
    const stage = page.getByRole('dialog', { name: '발표 모드' })
    await expect(stage).toBeVisible()
    await expect(stage.getByText('1 / 3')).toBeVisible()

    // What is on the wall is the file, not a redrawing of it: the seed's own
    // stylesheet travels with the slide, and only that slide is in the page.
    // Read off `srcdoc` rather than through the frame — it is `sandbox=""`,
    // which is what makes model-written markup safe to show at all.
    const shown = async () => (await stage.locator('iframe').getAttribute('srcdoc')) ?? ''
    let doc = await shown()
    expect(doc).toContain('scroll-snap-align')
    expect(doc).toContain('2026년 상반기 계획')
    expect((doc.match(/<section/g) ?? []).length).toBe(1)

    await page.keyboard.press('ArrowRight')
    await expect(stage.getByText('2 / 3')).toBeVisible()
    doc = await shown()
    expect(doc).toContain('야간 배치가 밀린다')
    expect(doc).not.toContain('고칠 것은 장비가 아니라 주기다')

    await page.keyboard.press('Escape')
    await expect(stage).toBeHidden()
    // Escape ends the presentation, not the panel behind it.
    await expect(panel.getByRole('button', { name: '발표' })).toBeVisible()
  })

  test('장 목록으로 원하는 장에 바로 간다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('button', { name: '발표' }).click()
    const stage = page.getByRole('dialog', { name: '발표 모드' })

    // Twenty slides are not walked one at a time with a room waiting, so the
    // deck's own order is on the presenter's screen.
    await stage.getByRole('button', { name: '장 목록' }).click()
    const list = stage.getByRole('navigation', { name: '장 목록' })
    await expect(list.getByText('지금의 문제')).toBeVisible()
    await list.getByRole('button', { name: '3번 장' }).click()
    await expect(stage.getByText('3 / 3')).toBeVisible()
    expect(await stage.locator('iframe').getAttribute('srcdoc')).toContain(
      '고칠 것은 장비가 아니라 주기다',
    )
  })

  test('문서 서식에는 발표 버튼이 없다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, docTitle)
    // The listing carries no markup for an HTML artifact, so wait until the
    // document itself is on screen — otherwise "no 발표 button" is only a
    // statement about an artifact that had not arrived yet.
    await expect(panel.locator('iframe')).toHaveAttribute('srcdoc', /한 장 요약/, {
      timeout: 15_000,
    })
    // The same panel, and the same reading the exporter makes: a one-pager has
    // no slides, so offering to present it would be a button with no room.
    await expect(panel.getByRole('button', { name: '내보내기', exact: true })).toBeVisible()
    await expect(panel.getByRole('button', { name: '발표' })).toHaveCount(0)
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

  // The first pass plans and stops; approving it is what starts the run this
  // test watches. Pressed and left running — waiting for it to finish would
  // hand this test a screen with nothing left on it to count.
  await approveOnce(page, 360_000)

  // Open while it runs, and counting down against the outline's own total —
  // not against the steps that happen to have arrived.
  const running = page.getByRole('button', { name: /작업 중/ })
  await expect(running).toBeVisible({ timeout: 180_000 })
  await expect(running).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText(/\d+개 남음/)).toBeVisible({ timeout: 180_000 })

  // Finished work stays on screen, struck through.
  await expect(page.locator('.line-through').first()).toBeVisible({ timeout: 180_000 })

  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 360_000 })

  // The finished deck opens its panel, and below 1024px that panel covers the
  // conversation rather than sitting beside it — so the work log is behind it.
  // Put it away first, the way somebody looking back at what ran would.
  const closePanel = page.locator('[data-panel="artifact"]').getByRole('button', { name: '닫기' })
  if (await closePanel.first().isVisible().catch(() => false)) await closePanel.first().click()

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
  const viewTab = panel.getByRole('tab', { name: '보기' })
  if (await viewTab.isVisible().catch(() => false)) await viewTab.click()

  // Three positions, walked by one button, and the button is named for what
  // pressing it does. A document opens at a reading width, folds the
  // conversation away, comes back to the narrow column, and round again.
  const only = panel.getByRole('button', { name: '문서만 보기' })
  await expect(only).toBeVisible()
  await only.click()
  const narrower = panel.getByRole('button', { name: '패널 좁히기' })
  await expect(narrower).toBeVisible()
  await narrower.click()
  const wider = panel.getByRole('button', { name: '넓게 보기' })
  await expect(wider).toBeVisible()
  await wider.click()
  await expect(only).toBeVisible()

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

test('분할 화면에서도 채팅 입력 영역이 찌그러지지 않는다', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await signIn(page)
  const session = await page.evaluate(async (fn) => {
    const rows = await eval(fn)('/api/sessions')
    const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
    return list.find((s: { artifactId: string | null }) => s.artifactId) ?? null
  }, AS_USER)
  test.skip(!session, '결과물이 붙은 대화가 아직 없습니다.')
  await page.goto(`/s/${session.id}`)
  const prompt = page.getByLabel('프롬프트 입력')
  await expect(prompt).toBeVisible({ timeout: 20_000 })
  const composer = prompt.locator('xpath=ancestor::div[contains(@class,"max-w-3xl")]')
  const box = await composer.boundingBox()
  expect(box?.width).toBeGreaterThanOrEqual(410)
  expect(await composer.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true)
})

test('1280px에서는 결과물이 채팅을 밀어 찌그러뜨리지 않고 겹쳐 열린다', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await signIn(page)
  const session = await page.evaluate(async (fn) => {
    const rows = await eval(fn)('/api/sessions')
    const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
    return list.find((s: { artifactId: string | null }) => s.artifactId) ?? null
  }, AS_USER)
  test.skip(!session, '결과물이 붙은 대화가 아직 없습니다.')
  await page.goto(`/s/${session.id}`)
  const panel = page.locator('[data-panel="artifact"]')
  await expect(panel).toBeVisible({ timeout: 20_000 })
  await expect(panel).toHaveCSS('position', 'absolute')
  const box = await panel.boundingBox()
  expect(box?.width).toBeGreaterThan(900)
  expect(await panel.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true)
})

const SECTIONS = [
  {
    id: 'r1',
    heading: '배경',
    level: 1,
    status: 'done',
    content: '전이학습은 적은 표본에서도 쓸 만한 표현을 빌려 온다 [1].',
  },
  {
    id: 'r2',
    heading: '한계',
    level: 1,
    status: 'done',
    content: '도메인이 멀어질수록 빌려 온 표현은 빠르게 쓸모를 잃는다 [9]. 실험 정확도는 87%였다.',
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
        sources: [
          {
            id: 'src1',
            ordinal: 1,
            title: '전이학습 검토 자료',
            author: '김연구',
            publisher: '한국연구원',
            year: '2026',
            url: 'https://example.org/transfer',
            origin: 'web',
            originLabel: '웹 검색',
          },
          {
            id: 'src2',
            ordinal: 2,
            title: '사용하지 않은 후보 자료',
            publisher: '후보 연구소',
            url: '',
            origin: 'file',
            originLabel: '프로젝트 파일',
            quote: '7쪽',
          },
        ],
        research: {
          enabled: true,
          searched: true,
          queries: ['전이학습 표본 효율 연구', '도메인 전이 성능 저하'],
          selected: 2,
          excluded: 3,
          webSelected: 1,
          projectSelected: 1,
          projectExcluded: 2,
        },
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
    await panel.getByRole('tab', { name: '검토' }).click()
    // Version history is reachable by the same name the restore flow uses.
    await expect(panel.getByRole('button', { name: '버전 기록' })).toBeVisible()
    await expect(panel.getByText('저장 시점 v1')).toBeVisible()
    // The count moved onto the button that opens the contents. It used to sit
    // in a column standing beside the document at every width, and that column
    // was 208px of the document's own room.
    await panel.getByRole('tab', { name: '보기' }).click()
    await expect(panel.getByRole('button', { name: /목차 \d+\/2/ })).toBeVisible()
  })

  test('리본 탭은 좌우·Home·End 키로 한 자리에서 이동한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    const menu = panel.getByRole('tablist', { name: '보고서 메뉴' })
    const home = menu.getByRole('tab', { name: '홈' })
    await home.focus()
    await expect(home).toHaveAttribute('tabindex', '0')
    await page.keyboard.press('ArrowRight')
    await expect(menu.getByRole('tab', { name: '삽입' })).toHaveAttribute('aria-selected', 'true')
    await page.keyboard.press('End')
    await expect(menu.getByRole('tab', { name: '파일' })).toHaveAttribute('aria-selected', 'true')
    await page.keyboard.press('Home')
    await expect(home).toHaveAttribute('aria-selected', 'true')
    await expect(menu.locator('[role="tab"][tabindex="0"]')).toHaveCount(1)
  })

  test('리본 드롭다운은 버튼 상태를 알리고 메뉴 안에서 초점을 돌려준다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    // 인상은 버튼 셋이 되었고(덱과 같게), 남은 드롭다운은 색 고르기다.
    const trigger = panel.getByRole('button', { name: '보고서 색 고르기' })
    await trigger.focus()
    await trigger.press('Enter')
    await expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const menu = page.getByRole('menu')
    await expect(menu).toBeVisible()
    const items = menu.getByRole('menuitemcheckbox')
    await expect(items.first()).toBeFocused()
    await page.keyboard.press('ArrowDown')
    await expect(items.nth(1)).toBeFocused()
    await page.keyboard.press('End')
    await expect(items.last()).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(menu).toHaveCount(0)
    await expect(trigger).toBeFocused()
    await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  test('320px 폭에서도 보고서 도구가 읽을 수 있는 크기로 줄을 바꾼다', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 720 })
    await signIn(page)
    const panel = await openPreview(page, title)
    for (const heading of await panel.locator(`[title="${title}"]`).all()) {
      expect((await heading.boundingBox())?.height).toBeLessThanOrEqual(24)
      await expect(heading).toHaveCSS('white-space', 'nowrap')
    }
    const webMenu = panel.getByRole('tablist', { name: '보고서 메뉴' })
    await expect(webMenu).toHaveCSS('display', 'flex')
    await expect(webMenu).toHaveCSS('overflow-x', 'auto')
    expect((await webMenu.boundingBox())?.height).toBeLessThanOrEqual(40)
    expect(await page.evaluate(async () => (await document.fonts.load('13px Pretendard', '한글')).length > 0)).toBe(true)
    expect(await page.evaluate(async () => (await document.fonts.load('13px Nanum Myeongjo', '한글')).length > 0)).toBe(true)
    for (const tab of ['홈', '삽입', '레이아웃', '검토', '보기', '파일']) await expect(webMenu.getByRole('tab', { name: tab })).toBeVisible()
    for (const name of ['문서 수정', '페이지뷰', '편집형', '보고서 색 고르기']) {
      const button = panel.getByRole('button', { name }).first()
      await expect(button).toBeVisible()
      const box = await button.boundingBox()
      expect(box?.height).toBeGreaterThanOrEqual(31.5)
      expect(box?.width).toBeGreaterThanOrEqual(31.5)
    }
    await panel.getByRole('button', { name: '보고서 색 고르기' }).click()
    const designMenu = page.getByRole('menu')
    await expect(designMenu).toBeVisible()
    const designMenuBox = await designMenu.boundingBox()
    expect(designMenuBox?.x).toBeGreaterThanOrEqual(0)
    expect((designMenuBox?.x ?? 0) + (designMenuBox?.width ?? 0)).toBeLessThanOrEqual(320)
    await page.keyboard.press('Escape')
    await webMenu.getByRole('tab', { name: '보기' }).click()
    await expect(panel.getByRole('button', { name: /목차 \d+\/2/ })).toBeVisible()
    await webMenu.getByRole('tab', { name: '파일' }).click()
    await expect(panel.getByRole('button', { name: '내보내기' })).toBeVisible()
    await webMenu.getByRole('tab', { name: '홈' }).click()
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '페이지뷰' }).click()
    const preview = panel.getByLabel('실제 페이지 미리보기')
    await expect(preview).toHaveAttribute('data-page-count', /\d+/, { timeout: 30_000 })
    const previewBox = await preview.boundingBox()
    expect(previewBox?.width).toBeLessThanOrEqual(304)
    expect(Number(await preview.getAttribute('data-page-scale'))).toBeLessThan(0.5)

    await panel.getByRole('button', { name: '내용 편집' }).click()
    const editPage = panel.getByLabel('보고서 편집 페이지')
    await expect(editPage).toBeVisible()
    // The artifact dialog itself has 8px gutters and borders; the document
    // keeps all remaining width instead of losing 224px to the outline.
    expect(await editPage.evaluate((node) => node.clientWidth)).toBeGreaterThanOrEqual(270)
    const toolbar = panel.getByLabel('서체').locator('..')
    expect(await toolbar.evaluate((node) => node.scrollWidth)).toBeGreaterThanOrEqual(await toolbar.evaluate((node) => node.clientWidth))
  })

  test('내용을 유지한 채 보고서 디자인 세 계열을 바꾼다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    // The design control is discoverable from the normal reading view. The
    // first choice opens the page view so the consequence is visible at once.
    // 인상은 리본에 버튼 셋으로 바로 있다 — 덱과 같은 모양이다.
    await expect(panel.getByRole('button', { name: '편집형', exact: true })).toBeVisible()

    const captures: Buffer[] = []
    for (const [buttonName, storedStyle] of [
      ['매거진형', 'poster'],
      ['미니멀', 'minimal'],
      ['편집형', 'editorial'],
    ] as const) {
      const saved = page.waitForResponse((response) => response.url().includes(`/api/artifacts/${reportId}`) && response.request().method() === 'PATCH')
      await panel.getByRole('button', { name: buttonName, exact: true }).click()
      await saved
      await expect(panel.getByRole('button', { name: buttonName, exact: true })).toHaveAttribute('aria-pressed', 'true')
      await expect(panel.getByRole('button', { name: '웹뷰' })).toBeVisible()
      const artifact = await page.evaluate(async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`), [AS_USER, reportId] as const)
      expect((artifact as { data: { design: { visualStyle: string } } }).data.design.visualStyle).toBe(storedStyle)
      captures.push(await panel.screenshot())
    }
    expect(captures[0].equals(captures[1])).toBe(false)
    expect(captures[1].equals(captures[2])).toBe(false)

    await panel.getByRole('button', { name: '보고서 색 고르기' }).click()
    const recoloured = page.waitForResponse((response) => response.url().includes(`/api/artifacts/${reportId}`) && response.request().method() === 'PATCH')
    await page.getByRole('menuitemcheckbox', { name: '청록', exact: true }).click()
    await recoloured
    const recolouredReport = await page.evaluate(async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`), [AS_USER, reportId] as const)
    expect((recolouredReport as { data: { design: { accent: string; visualStyle: string } } }).data.design).toMatchObject({ accent: '#0f766e', visualStyle: 'editorial' })
  })

  test('문서 수정은 페이지 미리보기가 아니라 내용 편집기를 바로 연다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '문서 수정' }).click()
    await expect(panel.getByLabel('보고서 편집 페이지')).toBeVisible()
    await expect(panel.getByRole('button', { name: '페이지 설정' })).toHaveCount(0)
    await panel.getByRole('tab', { name: '레이아웃' }).click()
    await panel.getByRole('button', { name: '페이지 설정' }).click()
    await expect(panel.getByLabel('페이지 설정 도구')).toBeVisible()
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

  test('너비는 세 자리를 돌고 제자리로 온다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('tab', { name: '보기' }).click()
    await panel.getByRole('button', { name: '문서만 보기' }).click()
    await panel.getByRole('button', { name: '패널 좁히기' }).click()
    await panel.getByRole('button', { name: '넓게 보기' }).click()
    // Back where it opened, which is the position a document should be in.
    await expect(panel.getByRole('button', { name: '문서만 보기' })).toBeVisible()
  })

  test('보고서 명령은 해당 리본에 한 번만 나타난다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await expect(panel.getByLabel('즐겨찾기')).toHaveCount(0)
    await expect(panel.getByRole('button', { name: '문서 수정' })).toHaveCount(1)
    await expect(panel.getByRole('button', { name: '페이지뷰' })).toHaveCount(1)
    await panel.getByRole('tab', { name: '검토' }).click()
    await expect(panel.getByRole('button', { name: /출처 2/ })).toHaveCount(1)
    await panel.getByRole('tab', { name: '레이아웃' }).click()
    await expect(panel.getByRole('button', { name: '페이지 설정' })).toHaveCount(1)
    await panel.getByRole('tab', { name: '보기' }).click()
    await expect(panel.getByRole('button', { name: /목차 \d+\/\d+/ })).toHaveCount(1)
    // 내보내기는 파일을 만드는 일이라 파일에, 저장 시점은 되돌아보는 일이라
    // 검토에 있다.
    await panel.getByRole('tab', { name: '파일' }).click()
    await expect(panel.getByRole('button', { name: '내보내기' })).toHaveCount(1)
    await panel.getByRole('tab', { name: '검토' }).click()
    await expect(panel.getByRole('button', { name: /버전 기록/ })).toHaveCount(1)
  })

  test('보고서 변경도 닫기 전에 버릴지 확인한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '문서 수정' }).click()
    const editor = panel.locator('.ProseMirror').first()
    await editor.click()
    await page.keyboard.type('미저장 ')
    await page.keyboard.press('Escape')
    const outerConfirm = page.getByRole('dialog', { name: '저장하지 않은 변경 내용이 있습니다' })
    await expect(outerConfirm).toBeVisible()
    await outerConfirm.getByRole('button', { name: '취소' }).click()
    await expect(editor).toBeVisible()
    await panel.getByLabel('빠른 도구').getByRole('button', { name: '닫기' }).click()
    const confirm = page.getByRole('dialog', { name: '저장하지 않은 변경 내용이 있습니다' })
    await expect(confirm).toBeVisible()
    await confirm.getByRole('button', { name: '취소' }).click()
    await expect(editor).toBeVisible()
    await panel.getByLabel('빠른 도구').getByRole('button', { name: '닫기' }).click()
    await confirm.getByRole('button', { name: '저장하지 않고 닫기' }).click()
    await expect(panel).toHaveCount(0)
  })

  test('페이지 편집 중 다른 저장이 들어오면 덮어쓰지 않고 로컬 편집을 남긴다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '문서 수정' }).click()
    const editor = panel.locator('.ProseMirror').first()
    await editor.click()
    await page.keyboard.type('로컬 편집 ')

    await page.evaluate(async ([fn, id]) => {
      const artifact = await eval(fn as string)(`/api/artifacts/${id}`)
      const data = artifact.data
      data.sections[1].content += ' 외부 편집 보존'
      await eval(fn as string)(`/api/artifacts/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data, expectedVersion: artifact.version }),
      })
    }, [AS_USER, reportId] as const)

    await panel.getByLabel('빠른 도구').getByRole('button', { name: '저장' }).click()
    await expect(panel.getByText(/다른 곳에서 이미 수정/)).toBeVisible()
    await expect(editor).toContainText('로컬 편집')
    const stored = await page.evaluate(async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`), [AS_USER, reportId] as const)
    expect((stored as { data: { sections: Array<{ content: string }> } }).data.sections[1].content).toContain('외부 편집 보존')
    expect((stored as { data: { sections: Array<{ content: string }> } }).data.sections[0].content).not.toContain('로컬 편집')

    await expect(panel.getByRole('button', { name: '내 편집 내용 복사' })).toBeVisible()
    await panel.getByRole('button', { name: '최신본 불러오기' }).click()
    await expect(panel.getByText(/다른 곳에서 이미 수정/)).toHaveCount(0)
    await expect(panel.locator('.ProseMirror').filter({ hasText: '외부 편집 보존' })).toHaveCount(1)
    await expect(editor).not.toContainText('로컬 편집')
  })

  test('원문 편집 중 다른 저장을 덮어쓰지 않고 최신본으로 복구한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    // 앞 사례가 서식을 입혀 두면 문서는 페이지뷰로 열리고, 원문 편집은 웹뷰의
    // 버튼이다. 어느 쪽으로 열렸든 웹뷰로 맞추고 연다.
    if (await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).isVisible().catch(() => false)) {
      await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).click()
    }
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '원문 편집' }).click()
    const source = panel.getByLabel('문서 원본')
    await source.fill(`${await source.inputValue()}\n\n로컬 원문 편집`)

    await page.evaluate(async ([fn, id]) => {
      const artifact = await eval(fn as string)(`/api/artifacts/${id}`)
      const data = artifact.data
      data.sections[1].content += ' 원문 외부 편집 보존'
      await eval(fn as string)(`/api/artifacts/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data, expectedVersion: artifact.version }),
      })
    }, [AS_USER, reportId] as const)

    await panel.getByLabel('빠른 도구').getByRole('button', { name: '저장' }).click()
    await expect(panel.getByText(/다른 곳에서 이미 수정/)).toBeVisible()
    await expect(source).toHaveValue(/로컬 원문 편집/)
    await expect(panel.getByRole('button', { name: '내 편집 내용 복사' })).toBeVisible()

    await panel.getByRole('button', { name: '최신본 불러오기' }).click()
    await expect(panel.getByText(/다른 곳에서 이미 수정/)).toHaveCount(0)
    await expect(source).toHaveValue(/원문 외부 편집 보존/)
    await expect(source).not.toHaveValue(/로컬 원문 편집/)
  })

  test('Ctrl+S로 보고서 원문 편집을 저장한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    // 앞 사례가 서식을 입혀 두면 문서는 페이지뷰로 열리고, 원문 편집은 웹뷰의
    // 버튼이다. 어느 쪽으로 열렸든 웹뷰로 맞추고 연다.
    if (await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).isVisible().catch(() => false)) {
      await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).click()
    }
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '원문 편집' }).click()
    const source = panel.getByLabel('문서 원본')
    await source.fill(`${await source.inputValue()}\n\n## 키보드 저장\nCtrl+S로 저장한 보고서`)
    const saved = page.waitForResponse((response) => response.url().includes(`/api/artifacts/${reportId}`) && response.request().method() === 'PATCH')
    await source.press('Control+s')
    await saved
    await expect(source).toHaveCount(0)
    await expect(panel.getByText('Ctrl+S로 저장한 보고서')).toBeVisible()
  })

  test('Ctrl+S로 보고서 페이지 편집을 저장한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '문서 수정' }).click()
    const editor = panel.locator('.ProseMirror').first()
    await editor.click()
    await page.keyboard.press('End')
    await page.keyboard.type(' 페이지 키보드 저장')
    const saveButton = panel.getByLabel('빠른 도구').getByRole('button', { name: '저장' })
    await expect(saveButton).toBeVisible()
    // 저장하지 않은 편집은 길을 막지 않는다. These used to be disabled while
    // anything was unsaved, so one keystroke greyed out the way back to the
    // web view and the design menu with nothing saying why — the 저장 that
    // would free them sitting in a different row. They stay usable and commit
    // the text on the way through; the guard that matters is beforeunload.
    const webViewButton = panel.getByRole('button').filter({ hasText: /^웹뷰$/ })
    await expect(webViewButton).toBeEnabled()
    await expect(panel.getByRole('button', { name: '편집형', exact: true })).toBeEnabled()
    expect(await page.evaluate(() => window.dispatchEvent(new Event('beforeunload', { cancelable: true })))).toBe(false)
    const saved = page.waitForResponse((response) => response.url().includes(`/api/artifacts/${reportId}`) && response.request().method() === 'PATCH')
    await editor.press('Control+s')
    await saved
    await expect(saveButton).toHaveCount(0)
    expect(await page.evaluate(() => window.dispatchEvent(new Event('beforeunload', { cancelable: true })))).toBe(true)
    const stored = await page.evaluate(async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`), [AS_USER, reportId] as const)
    expect((stored as { data: { sections: Array<{ content: string }> } }).data.sections[0].content).toContain('페이지 키보드 저장')
  })

  test('웹뷰로 넘어가면 쓰던 것이 먼저 저장된다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '문서 수정' }).click()
    const editor = panel.locator('.ProseMirror').first()
    await editor.click()
    await page.keyboard.press('End')
    await page.keyboard.type(' 넘어가며 저장')
    const saved = page.waitForResponse((response) => response.url().includes(`/api/artifacts/${reportId}`) && response.request().method() === 'PATCH')
    await panel.getByRole('button').filter({ hasText: /^웹뷰$/ }).click()
    await saved
    const stored = await page.evaluate(async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`), [AS_USER, reportId] as const)
    expect((stored as { data: { sections: Array<{ content: string }> } }).data.sections[0].content).toContain('넘어가며 저장')
  })

  test('이전 보고서를 확인 후 복원하고 바로 다시 편집한다', async ({ page }) => {
    await signIn(page)
    const before = await page.evaluate(async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`), [AS_USER, reportId] as const) as { version: number; data: { sections: typeof SECTIONS } }
    const changed = structuredClone(before.data)
    changed.sections[0].content += ' 복원하면 사라질 보고서 문장'
    ;[changed.sections[0], changed.sections[1]] = [changed.sections[1], changed.sections[0]]
    await page.evaluate(async ([fn, id, data, version]) => await eval(fn as string)(`/api/artifacts/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data, expectedVersion: version }),
    }), [AS_USER, reportId, changed, before.version] as const)

    const panel = await openPreview(page, title)
    // 앞 사례가 서식을 입혀 두면 문서는 페이지뷰로 열리고, 원문 편집은 웹뷰의
    // 버튼이다. 어느 쪽으로 열렸든 웹뷰로 맞추고 연다.
    if (await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).isVisible().catch(() => false)) {
      await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).click()
    }
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '원문 편집' }).click()
    await panel.getByLabel('문서 원본').fill('저장하지 않은 보고서 편집')
    await panel.getByRole('tab', { name: '검토' }).click()
    await panel.getByRole('button', { name: /버전 기록/ }).click()
    const history = page.getByRole('dialog', { name: '버전 기록' })
    await history.getByRole('button', { name: `v${before.version} 내용 보기` }).click()
    await expect(history.getByLabel(`v${before.version} 내용 미리보기`)).toContainText(before.data.sections[0].content.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim())
    await expect(history.getByLabel('현재 판과 변경 비교')).toContainText('수정 1절')
    await expect(history.getByLabel('현재 판과 변경 비교')).toContainText('이동 2절')
    await history.getByRole('button', { name: `v${before.version} 로 되돌리기` }).click()
    const confirm = page.getByRole('dialog', { name: `v${before.version}으로 되돌릴까요?` })
    await expect(confirm).toContainText('저장하지 않은 변경 내용은 사라집니다')
    await confirm.getByRole('button', { name: '이 버전으로 복원' }).click()
    await expect(panel.getByLabel('문서 원본')).toHaveCount(0)
    await expect(panel.getByText('복원하면 사라질 보고서 문장')).toHaveCount(0)

    await panel.getByRole('tab', { name: '홈' }).click()
    // 앞 사례가 서식을 입혀 두면 문서는 페이지뷰로 열리고, 원문 편집은 웹뷰의
    // 버튼이다. 어느 쪽으로 열렸든 웹뷰로 맞추고 연다.
    if (await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).isVisible().catch(() => false)) {
      await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).click()
    }
    await panel.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '원문 편집' }).click()
    const source = panel.getByLabel('문서 원본')
    await source.fill(`${await source.inputValue()}\n\n## 복원 뒤 재저장\n정상 저장 확인`)
    await panel.getByLabel('빠른 도구').getByRole('button', { name: '저장' }).click()
    await expect(source).toHaveCount(0)
  })

  test('출처에서 사용된 절과 사용되지 않은 자료를 구별하고 본문으로 돌아간다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('tab', { name: '검토' }).click()
    await expect(panel.getByRole('button', { name: /출처 2 · 확인 2/ })).toBeVisible()
    await panel.getByRole('tab', { name: '파일' }).click()
    await panel.getByRole('button', { name: '내보내기' }).click()
    await expect(page.getByText('내보내기 전 근거 확인 2건')).toBeVisible()
    await page.getByRole('menuitem', { name: '먼저 근거 확인' }).click()

    await expect(panel.getByText('본문에서 사용')).toHaveCount(2)
    await expect(panel.getByText('인용되지 않음')).toBeVisible()
    await expect(panel.getByText('자료 1/2개 사용')).toBeVisible()
    await expect(panel.getByText('목록에 없는 인용 [9]')).toBeVisible()
    await expect(panel.getByText('근거 표시가 필요한 수치 문장 1개')).toBeVisible()
    await expect(panel.getByRole('button', { name: '한계', exact: true })).toBeVisible()
    await panel.getByRole('button', { name: '배경', exact: true }).click()
    await expect(panel.getByText('전이학습은 적은 표본에서도', { exact: false })).toBeVisible()
    await expect(panel.getByRole('heading', { name: '참고문헌' })).toHaveCount(0)
  })

  test('직접 확인한 자료를 추가하고 미사용 자료만 정리한다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('tab', { name: '검토' }).click()
    await panel.getByRole('button', { name: '출처' }).click()
    await expect(panel.getByTestId('research-log')).toContainText('검색어 2개 · 채택 2건 · 제외 3건')
    await expect(panel.getByTestId('research-log')).toContainText('전이학습 표본 효율 연구')
    await expect(panel.getByTestId('research-log')).toContainText('프로젝트 자료 1건 사용')
    await expect(panel.getByTestId('research-log')).toContainText('분량 때문에 제외 2건')
    await expect(panel.getByText('프로젝트 파일')).toBeVisible()
    await expect(panel.getByText('7쪽')).toBeVisible()
    await panel.getByRole('button', { name: '자료 추가' }).click()

    const modal = page.getByRole('dialog', { name: '참고 자료 추가' })
    await modal.getByLabel('자료 제목').fill('직접 확인한 기관 자료')
    await modal.getByLabel('원문 주소').fill('https://example.org/manual')
    await modal.getByLabel('발행처').fill('공식 기관')
    await modal.getByRole('button', { name: '추가', exact: true }).click()
    await expect(modal).toHaveCount(0)
    await expect(panel.getByText('직접 확인한 기관 자료')).toBeVisible()
    await expect(panel.getByText('직접 추가')).toBeVisible()

    await panel.getByRole('button', { name: '직접 확인한 기관 자료 자료 삭제' }).click()
    await expect(panel.getByText('직접 확인한 기관 자료')).toHaveCount(0)
    // [1]이 붙은 첫 자료에는 삭제 버튼 자체가 없다.
    await expect(panel.getByRole('button', { name: '전이학습 검토 자료 자료 삭제' })).toHaveCount(0)
  })

  test('인용 형식을 바꾸면 참고문헌과 저장 데이터가 함께 바뀐다', async ({ page }) => {
    await signIn(page)
    const panel = await openPreview(page, title)
    await panel.getByRole('tab', { name: '검토' }).click()
    await panel.getByRole('button', { name: '출처' }).click()

    await expect(panel.getByText('김연구. (2026). 전이학습 검토 자료')).toBeVisible()
    const saved = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/artifacts/${reportId}`) &&
        response.request().method() === 'PATCH',
    )
    await panel.getByRole('button', { name: '인용 형식' }).click()
    await page.getByRole('menuitemcheckbox', { name: 'IEEE' }).click()
    await saved

    await expect(panel.getByText('[1] 김연구, “전이학습 검토 자료.”')).toBeVisible()
    const stored = await page.evaluate(
      async ([fn, id]) => await eval(fn as string)(`/api/artifacts/${id}`),
      [AS_USER, reportId] as const,
    )
    expect((stored as { data: { citationStyle: string } }).data.citationStyle).toBe('IEEE')
  })
})
