import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { approvePlan, deckHeader, deckNotes, deckPanel, deckThumbs, deckTitle, deckType, saveDeck, signIn } from './helpers'

/** Entry names from a zip's central directory; nothing is inflated. */
function zipNames(buffer: Buffer): string[] {
  const names: string[] = []
  for (let i = 0; i < buffer.length - 4; i++) {
    if (buffer.readUInt32LE(i) !== 0x02014b50) continue // central directory header
    const length = buffer.readUInt16LE(i + 28)
    names.push(buffer.toString('utf8', i + 46, i + 46 + length))
  }
  return names
}

/** The slides surface builds a deck; one generation per run, everything asserted against it. */

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

  // Nothing is written until the plan is approved.
  await approvePlan(page)

  // The outline lands first: the whole deck is on screen before any slide is written,
  // a numbered thumbnail per slide in the rail.
  await expect(deckThumbs(page).first()).toBeVisible({ timeout: 120_000 })

  // 내보내기 stays disabled while any slide is still empty.
  const exportButton = deckHeader(page).getByRole('button', { name: '내려받기', exact: true })
  await expect(exportButton).toBeEnabled({ timeout: 360_000 })

  // Found by session (the account is shared) and read by id (listing rows are partial).
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
  expect(stored.partial, '목록 카드가 아니라 덱 전체를 읽어야 합니다').toBeFalsy()

  const slides = stored.data.slides as {
    layout: string
    title: string
    body?: string
    bullets?: string[]
    rows?: string[][]
    metrics?: [string, string][]
    bands?: [string, string][]
    tiles?: [string, string][]
    timeline?: [string, string][]
    steps?: [string, string][]
    cards?: [string, string][]
    chart?: unknown
  }[]
  expect(slides.length).toBeGreaterThanOrEqual(5)
  expect(slides[0].layout).toBe('title')

  // The subtitle is not the request echoed back.
  expect(slides[0].body ?? '').not.toContain('만들어줘')

  // Only layouts every output (preview, .pptx, .pdf) can draw: `deck._LAYOUTS`.
  for (const slide of slides) {
    expect([
      'title', 'section', 'agenda', 'bullets', 'quote', 'statement', 'two-column', 'table',
      'metrics', 'big-number', 'chart',
      'bands', 'tiles', 'timeline', 'steps', 'cards', 'closing',
    ]).toContain(
      slide.layout,
    )
  }
  // Every non-cover slide has content in whichever shape it chose.
  for (const slide of slides.slice(1)) {
    const said =
      (slide.bullets?.length ?? 0) +
      (slide.body ? 1 : 0) +
      (slide.rows?.length ?? 0) +
      (slide.metrics?.length ?? 0) +
      (slide.chart ? 1 : 0) +
      (slide.bands?.length ?? 0) +
      (slide.tiles?.length ?? 0) +
      (slide.timeline?.length ?? 0) +
      (slide.steps?.length ?? 0) +
      (slide.cards?.length ?? 0)
    expect(said, `${slide.layout} 장이 비어 있다: ${slide.title}`).toBeGreaterThan(0)
  }

  expect(stored.title).not.toContain('만들어줘')

  await expect(deckThumbs(page)).toHaveCount(slides.length)

  const download = page.waitForEvent('download', { timeout: 60_000 })
  await exportButton.click()
  await page.getByRole('menuitem', { name: 'PowerPoint' }).click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/\.pptx$/)

  // A .pptx is a zip with one slide part per slide.
  const zip = await readFile(await file.path())
  const names = zipNames(zip)
  expect(names.filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))).toHaveLength(slides.length)
  // Notes go into the notes pane.
  expect(names.some((n) => n.startsWith('ppt/notesSlides/notesSlide'))).toBe(true)
})

test('슬라이드 한 장을 고치면 저장되고 새로고침 뒤에도 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  const card = page.locator('button.aspect-video').first()
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 20_000 })
  // The slide is editable in place: no 편집 toggle, nothing saved until 저장.
  await expect(deckThumbs(page).first()).toBeVisible({ timeout: 20_000 })
  await deckThumbs(page).first().click()

  const edited = `수정한 제목 ${Date.now()}`
  await deckType(deckTitle(page), edited)
  await deckNotes(page).fill('여기서는 이렇게 말한다')
  await deckNotes(page).blur()
  await expect(deckHeader(page).getByText('저장 안 됨')).toBeVisible()
  await saveDeck(page)

  // A refused save says so and keeps the edits unsaved; check for that first.
  await expect(deckPanel(page).getByText('그사이 다른 판이 저장되어 저장하지 않았습니다.')).toHaveCount(0)
  await expect(deckNotes(page)).toHaveValue('여기서는 이렇게 말한다')
  await expect(deckTitle(page)).toHaveText(edited)

  // Survives a reload: the server has it.
  await page.reload()
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  // Found by content: the gallery sorts by last touched.
  const again = page.locator('button.aspect-video').filter({ hasText: edited }).first()
  await expect(again).toBeVisible({ timeout: 20_000 })
  await again.click()
  await expect(page.getByText(edited).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('heading', { name: '화면을 표시하지 못했습니다' })).toHaveCount(0)
})
