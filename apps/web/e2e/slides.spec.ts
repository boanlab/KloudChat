import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

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

  // The outline lands first, so the whole deck is on screen — greyed out —
  // before any of it is written. That is the point of the two-pass split.
  await expect(page.getByText(/^\d+장$/)).toBeVisible({ timeout: 120_000 })

  // Then the slides fill in. Waiting on the export button is waiting on the
  // last slide: it stays disabled while any slide is still empty.
  const exportButton = page.getByRole('button', { name: '내보내기' })
  await expect(exportButton).toBeEnabled({ timeout: 360_000 })

  const stored = await page.evaluate(async (fn) => {
    const rows = await eval(fn)('/api/artifacts')
    const list = Array.isArray(rows) ? rows : rows.items
    return list.find((a: { kind: string }) => a.kind === 'deck') ?? null
  }, AS_USER)
  expect(stored, '덱 아티팩트가 없습니다').not.toBeNull()

  const slides = stored.data.slides as {
    layout: string
    title: string
    body?: string
    bullets?: string[]
  }[]
  expect(slides.length).toBeGreaterThanOrEqual(5)
  expect(slides[0].layout).toBe('title')

    // The title slide's subtitle is written fresh, not the request echoed
    // back.
  expect(slides[0].body ?? '').not.toContain('만들어줘')

  // Only layouts with a renderer behind them. `chart` in particular would draw
  // five hard-coded bars — invented numbers, on a slide, in front of a room.
  for (const slide of slides) {
    expect(['title', 'bullets', 'quote']).toContain(slide.layout)
  }
  // Every non-cover slide actually says something.
  for (const slide of slides.slice(1)) {
    expect((slide.bullets?.length ?? 0) + (slide.body ? 1 : 0)).toBeGreaterThan(0)
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
  // The deck made by the test above, opened from its card on the artifacts
  // screen: filter to decks, then open the newest one.
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await expect(page.getByRole('button', { name: '내보내기' })).toBeVisible({ timeout: 20_000 })

  const edited = `수정한 제목 ${Date.now()}`
  await page.getByRole('button', { name: '텍스트 수정' }).click()
  await page.getByLabel('슬라이드 텍스트').fill(`${edited}\n첫째 항목\n둘째 항목`)
  await page.getByLabel('발표 노트').fill('여기서는 이렇게 말한다')
  await page.getByRole('button', { name: '저장', exact: true }).click()

  await expect(page.getByText('여기서는 이렇게 말한다')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(edited).first()).toBeVisible()

  // Survives a reload, i.e. the server has it — not the panel mutating its own
  // copy and calling that a save.
  await page.reload()
  await page.getByRole('tab', { name: /^슬라이드/ }).click()
  await page.locator('button.aspect-video').first().click()
  await expect(page.getByText(edited).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('heading', { name: '화면을 표시하지 못했습니다' })).toHaveCount(0)
})
