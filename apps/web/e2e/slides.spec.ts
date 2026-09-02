import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { approvePlan, signIn } from './helpers'

/**
 * The names in a zip's central directory, without pulling in a zip library.
 *
 * Only the names are needed — whether the parts exist and how many slides there
 * are — so the entries never have to be inflated.
 */
function zipNames(buffer: Buffer): string[] {
  const names: string[] = []
  for (let i = 0; i < buffer.length - 4; i++) {
    if (buffer.readUInt32LE(i) !== 0x02014b50) continue // central directory header
    const length = buffer.readUInt16LE(i + 28)
    names.push(buffer.toString('utf8', i + 46, i + 46 + length))
  }
  return names
}

/**
 * The slides surface builds a deck.
 *
 * One deck per run: it costs one model call for the outline and one per slide,
 * so the deck is generated once and everything is asserted against it.
 */

/** The API uses a bearer token held in memory, so a cookie fetch is anonymous. */
const AS_USER = `async (path) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + accessToken } })
  return r.ok ? await r.json() : null
}`

test('슬라이드를 만들면 장별로 채워지고 pptx 로 받을 수 있다', async ({ page }) => {
  test.setTimeout(420_000)
  await signIn(page)

  await page.goto('/new/slides')
  await page
    .getByLabel('프롬프트 입력')
    .fill('연구실 신입생에게 파이썬 가상환경 관리를 설명하는 발표 슬라이드를 만들어줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]

  // The first pass plans and stops, and nothing is written until the plan is
  // approved — so the deck cannot appear before this.
  await approvePlan(page)

  // The outline lands first, so the whole deck is on screen — greyed out —
  // before any of it is written. That is the point of the two-pass split.
  await page.getByRole('tab', { name: '보기', exact: true }).click({ timeout: 120_000 })
  // 「n장」 배지는 없앴다 — 옆의 목록이 이미 그 수를 그린다. 세는 일은
  // 장 목록 손잡이가 한다: 「장 목록 1/8」.
  await expect(page.getByRole('button', { name: '장 목록' })).toHaveText(/\d+\/[1-9]\d*/, { timeout: 120_000 })

  // Then the slides fill in. Waiting on the export button is waiting on the
  // last slide: it stays disabled while any slide is still empty.
  await page.getByRole('tab', { name: '파일', exact: true }).click()
  const exportButton = page.getByRole('button', { name: '내보내기', exact: true })
  await expect(exportButton).toBeEnabled({ timeout: 360_000 })

  // Two things sit between this test and the deck it just made. A listing row
  // is a card — four slide titles, no bodies — so the deck has to be read by
  // id. And the account is shared, so "the first deck" is whichever deck was
  // touched most recently by anybody; this one is found by its session.
  const stored = await page.evaluate(
    async ([fn, id]) => {
      const asUser = eval(fn)
      const rows = await asUser('/api/artifacts?kind=deck')
      const list = Array.isArray(rows) ? rows : rows.items
      const row = list.find((a: { sessionId: string }) => a.sessionId === id)
      return row ? await asUser('/api/artifacts/' + row.id) : null
    },
    [AS_USER, sessionId],
  )
  expect(stored, '이 세션의 덱 아티팩트가 없습니다').not.toBeNull()
  // The document, not a card of it — every assertion below reads a slide body.
  expect(stored.partial, '목록 카드가 아니라 덱 전체를 읽어야 합니다').toBeFalsy()

  const slides = stored.data.slides as {
    layout: string
    title: string
    body?: string
    bullets?: string[]
    rows?: string[][]
    metrics?: [string, string][]
    chart?: unknown
  }[]
  expect(slides.length).toBeGreaterThanOrEqual(5)
  expect(slides[0].layout).toBe('title')

    // The title slide's subtitle is written fresh, not the request echoed
    // back.
  expect(slides[0].body ?? '').not.toContain('만들어줘')

  // Only layouts with a renderer behind them in all three outputs — preview,
  // .pptx and .pdf. That used to be four; a table, a figure strip and a chart
  // have since been given one in each, so the list is `deck._LAYOUTS`. The
  // check is still worth making: a layout the writer may choose and one of the
  // three surfaces cannot draw is a blank rectangle in front of a room.
  //
  // `chart` was the one held back longest, because a chart with no numbers
  // behind it draws five invented bars. It is offered now only when the
  // source it is written from actually carries figures.
  for (const slide of slides) {
    expect([
      'title', 'section', 'bullets', 'quote', 'two-column', 'table', 'metrics', 'chart',
      // 이 가지에서 더해진 세 쌍 모양 — 이름표 줄·표식·연혁.
      'bands', 'tiles', 'timeline',
    ]).toContain(
      slide.layout,
    )
  }
  // Every non-cover slide actually says something — in whichever of the five
  // shapes it chose. Counting bullets and body alone would have called a
  // perfectly good table empty.
  for (const slide of slides.slice(1)) {
    const said =
      (slide.bullets?.length ?? 0) +
      (slide.body ? 1 : 0) +
      (slide.rows?.length ?? 0) +
      (slide.metrics?.length ?? 0) +
      (slide.chart ? 1 : 0) +
      // 세 쌍 모양도 말이다 — 이 합계가 쌍을 안 세서, 꽉 찬 연혁 장이
      // "비어 있다" 로 읽혔다.
      (slide.bands?.length ?? 0) +
      (slide.tiles?.length ?? 0) +
      (slide.timeline?.length ?? 0)
    expect(said, `${slide.layout} 장이 비어 있다: ${slide.title}`).toBeGreaterThan(0)
  }

  // The title is the model's, not the prompt.
  expect(stored.title).not.toContain('만들어줘')

  // Every slide has a thumbnail in the grid.
  await expect(page.locator('button.aspect-video')).toHaveCount(slides.length)

  // The export menu is wired. All three items were decoration before.
  const download = page.waitForEvent('download', { timeout: 60_000 })
  await exportButton.click()
  await page.getByRole('menuitem', { name: 'PowerPoint' }).click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/\.pptx$/)

  // A .pptx is a zip whose slide parts are one per slide. A file that is the
  // right size but has no slides in it is the failure this catches.
  const zip = await readFile(await file.path())
  const names = zipNames(zip)
  expect(names.filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))).toHaveLength(slides.length)
  // Notes are what the presenter reads. They went into the PowerPoint notes
  // pane, not onto the slide.
  expect(names.some((n) => n.startsWith('ppt/notesSlides/notesSlide'))).toBe(true)
})

test('슬라이드 한 장을 고치면 저장되고 새로고침 뒤에도 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/artifacts')
  // Filter to decks, then open one *by name*. Position is not identity here:
  // the gallery sorts by when a thing was last touched, so the card this test
  // edits moves to the front — and the list settles in more than one paint, so
  // "whichever is first right now" can be a different deck before and after.
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  const card = page.locator('button.aspect-video').first()
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('button', { name: '편집 도구' })).toBeVisible({ timeout: 20_000 })

  const edited = `수정한 제목 ${Date.now()}`
  await page.getByRole('button', { name: '편집 도구' }).click()
  await page.getByLabel('슬라이드 텍스트').fill(`${edited}\n첫째 항목\n둘째 항목`)
  await page.getByLabel('발표 노트').fill('여기서는 이렇게 말한다')
  await page.getByRole('button', { name: '저장', exact: true }).click()

  // Asserted before anything else: a refused save leaves the editor open, and
  // both checks below pass anyway — the note is already on the stored slide
  // from an earlier run, and the new title is sitting in the textarea. Without
  // this the failure surfaces three steps later as "the card is missing".
  await expect(page.getByText(/다른 곳에서 이미 수정/)).toHaveCount(0)
  await expect(page.getByLabel('슬라이드 텍스트')).toHaveCount(0, { timeout: 20_000 })
  await expect(page.getByText('여기서는 이렇게 말한다')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(edited).first()).toBeVisible()

  // Survives a reload, i.e. the server has it — not the panel mutating its own
  // copy and calling that a save.
  await page.reload()
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  // The same deck, found by what it says rather than where it sits.
  const again = page.locator('button.aspect-video').filter({ hasText: edited }).first()
  await expect(again).toBeVisible({ timeout: 20_000 })
  await again.click()
  await expect(page.getByText(edited).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('heading', { name: '화면을 표시하지 못했습니다' })).toHaveCount(0)
})
