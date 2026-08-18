import { readFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/**
 * The names in a zip's central directory, without pulling in a zip library.
 * Same reader as `slides.spec.ts` — only the names are needed.
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
 * The rendering catalogue, from the card to the file.
 *
 * A design template is a shape rather than a sentence: picking one replaces
 * the surface's built-in track, so what this walks is the third document path
 * — outline, one call per block, one HTML artifact — and not the markdown and
 * JSON ones the other specs cover.
 *
 * One deck and one document per run. The image surface is exercised as far as
 * the pick: the template only shapes a prompt, and generating the picture to
 * see it would cost ~4,400 credits for something with nothing to assert. That
 * composition is pinned in `tests/test_design_templates.py`.
 *
 * A few `shot()` calls write the states worth looking at into
 * `test-results/shots/`. They are for a person reading the run afterwards —
 * "the deck really came out in that template" is a claim a screenshot settles
 * and an assertion only narrows.
 */

/** A named screenshot, filed where the rest of the run's output goes. */
async function shot(page: Page, name: string) {
  await page.screenshot({ path: `test-results/shots/${name}.png`, fullPage: false })
}

/**
 * Opens the gallery and waits for its preview documents to arrive.
 *
 * The frames are `sandbox=""`, so a test cannot read into them to know they
 * painted — waiting on the responses that fill them is the closest honest
 * signal, and without it a screenshot catches two empty boxes.
 */
async function openGallery(page: Page, ids: string[]) {
  // Reopened within one test on the a/v surface, where four templates share
  // the gallery — the responses are cached after the first open, so only the
  // click is guaranteed to be observable.
  const previews = Promise.all(
    ids.map((id) =>
      page.waitForResponse((r) => r.url().includes(`/design-templates/${id}/preview`), {
        timeout: 20_000,
      }),
    ),
  )
  await page.getByRole('button', { name: '디자인 고르기' }).click()
  // Not fatal: a gallery opened a second time in one test serves its previews
  // from the browser's cache, and a cache hit is not a network response.
  await previews.catch(() => undefined)
  // The response is not the paint. Nothing observable sits between them
  // through a sandboxed frame, so this is a settle rather than a wait on
  // state — it only affects what the screenshot shows.
  await page.waitForTimeout(500)
  return page.getByRole('dialog')
}

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

async function artifactOf(page: Page, sessionId: string) {
  return page.evaluate(
    async ([fn, id]) => {
      const rows = await eval(fn)('/api/artifacts')
      const list = Array.isArray(rows) ? rows : rows.items
      return list.find((a: { sessionId: string }) => a.sessionId === id) ?? null
    },
    [AS_USER, sessionId],
  )
}

test('덱 디자인을 고르면 그 템플릿의 HTML 이 나오고 파일로 받을 수 있다', async ({ page }) => {
  test.setTimeout(600_000)
  await signIn(page)

  // ── 1. The gallery shows each template's own shape ──────────────────
  await page.goto('/new/slides')
  const gallery = await openGallery(page, ['deck-editorial', 'deck-signal'])
  const card = gallery.locator('div.group', { hasText: '편집형 덱' })
  await expect(card).toBeVisible({ timeout: 20_000 })

  // The preview is the seed rendered around its sample, served as a document.
  // Fetched rather than read through the frame: `sandbox=""` makes the frame
  // opaque to the test for the same reason it makes it safe.
  // Every card is that template's own seed, filled with its own sample.
  await expect(gallery.locator('iframe')).toHaveCount(2)
  await shot(page, '01-deck-gallery')

  const previewUrl = await card.locator('iframe').getAttribute('src')
  expect(previewUrl).toContain('/design-templates/deck-editorial/preview')
  const preview = await page.request.get(previewUrl!)
  expect(preview.status()).toBe(200)
  const previewHtml = await preview.text()
  expect(previewHtml).toContain('class="slide cover"')
  expect(previewHtml).toContain('--accent:')
  // A seed that shipped with a placeholder left in would render it literally.
  expect(previewHtml).not.toContain('{{')

  // ── 2. Picking one fills the composer and names itself ──────────────
  await card.getByRole('button', { name: '이 디자인으로 시작' }).click()
  await expect(gallery).toBeHidden()
  await expect(page.getByLabel('프롬프트 입력')).not.toHaveValue('')
  await expect(page.getByText('편집형 덱', { exact: true })).toBeVisible()
  await shot(page, '02-deck-picked')

  // ── 3. The turn writes into that template ───────────────────────────
  await page.getByLabel('프롬프트 입력').fill('연구실 장비 점검 결과를 발표할 자료를 만들어줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]

  // The stop control is the finish line, not the download button: the draft
  // artifact exists from the first event, so its button is on screen while the
  // blocks are still being written — and reading the stored artifact then
  // aborts the stream that would have saved it.
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 480_000 })
  const exportButton = page.getByRole('button', { name: '내보내기', exact: true })
  await expect(exportButton).toBeVisible({ timeout: 20_000 })

  await shot(page, '03-deck-rendered')

  const stored = await artifactOf(page, sessionId)
  expect(stored, '이 세션의 아티팩트가 없습니다').not.toBeNull()
  expect(stored.kind).toBe('html')
  expect(stored.data.templateId).toBe('deck-editorial')
  expect(stored.data.blocks.length).toBeGreaterThanOrEqual(4)
  expect(stored.data.blocks[0].layout).toBe('cover')

  const html = stored.data.content as string
  expect(html).toContain('<section class="slide cover">')
  expect(html).toContain('@media print')
  // The model writes content, never layout: nothing it sent may carry script.
  expect(html.toLowerCase()).not.toContain('<script')
  // Every block landed inside a styled section rather than loose in the body.
  expect((html.match(/<section class="slide/g) ?? []).length).toBeGreaterThanOrEqual(4)

  // ── 4. The file is the artifact… ────────────────────────────────────
  const savedHtml = page.waitForEvent('download', { timeout: 60_000 })
  await exportButton.click()
  await page.getByRole('menuitem', { name: '원본 HTML' }).click()
  const htmlFile = await savedHtml
  expect(htmlFile.suggestedFilename()).toMatch(/\.html$/)
  expect(await readFile(await htmlFile.path(), 'utf8')).toContain(
    '<section class="slide cover">',
  )

  // ── …and PowerPoint gets the same deck as editable slides ───────────
  // Read back out of the markup by `page_export`, so this is the assertion
  // that the conversion produced one slide part per section rather than a
  // single page with the whole file poured onto it.
  const savedPptx = page.waitForEvent('download', { timeout: 60_000 })
  await exportButton.click()
  await page.getByRole('menuitem', { name: 'PowerPoint' }).click()
  const pptxFile = await savedPptx
  expect(pptxFile.suggestedFilename()).toMatch(/\.pptx$/)
  const parts = zipNames(await readFile(await pptxFile.path()))
  expect(parts.filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))).toHaveLength(
    stored.data.blocks.length,
  )

  // ── 5. Taking the shape off reaches the row that holds it ───────────
  // The turn made the choice sticky, so a chip that only disappeared locally
  // would leave the next turn writing into a template nobody can see.
  await page.getByRole('button', { name: /편집형 덱 디자인 해제/ }).click()
  await expect(page.getByText('편집형 덱', { exact: true })).toHaveCount(0)
  await expect
    .poll(
      async () =>
        (
          await page.evaluate(
            async ([fn, id]) => await eval(fn)(`/api/sessions/${id}`),
            [AS_USER, sessionId],
          )
        )?.renderTemplateId,
      { timeout: 20_000 },
    )
    .toBeNull()
})

test('문서 디자인은 문서 조판으로 나온다', async ({ page }) => {
  test.setTimeout(600_000)
  await signIn(page)

  await page.goto('/new/report')
  const gallery = await openGallery(page, ['doc-report', 'doc-brief'])
  const card = gallery.locator('div.group', { hasText: '한 장 요약' })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await shot(page, '04-document-gallery')
  await card.getByRole('button', { name: '이 디자인으로 시작' }).click()

  await page.getByLabel('프롬프트 입력').fill('학과 서버를 교체할지 정하는 한 장 요약을 써줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]

  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 480_000 })
  await expect(page.getByRole('button', { name: '내보내기', exact: true })).toBeVisible({
    timeout: 20_000,
  })

  await shot(page, '05-document-rendered')

  const stored = await artifactOf(page, sessionId)
  expect(stored.data.templateId).toBe('doc-brief')
  const html = stored.data.content as string
  // The one-pager's own shape: a cover outside the grid, cards inside it.
  expect(html.indexOf('<div class="cover">')).toBeLessThan(html.indexOf('<div class="grid">'))
  expect(html).toContain('break-inside: avoid')
  // A document, not a deck — the slide vocabulary belongs to the other seed.
  expect(html).not.toContain('class="slide')
})

test('이미지·영상 템플릿은 빈칸을 채워 문장을 완성하고 옵션까지 맞춰 준다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  // ── image: blanks become a sentence, and the chips follow ───────────
  await page.goto('/new/image')
  const imageGallery = await openGallery(page, ['image-poster', 'image-cover'])
  const poster = imageGallery.locator('div.group', { hasText: '포스터' })
  await expect(poster).toBeVisible({ timeout: 20_000 })

  // Every blank starts filled, so the card is usable without typing.
  await expect(poster.getByLabel('무엇을')).toHaveValue('학과 연구 성과 발표회')
  await poster.getByLabel('무엇을').fill('연구실 개방 행사')
  await poster.getByLabel('분위기').selectOption('밝고 활기찬')
  await shot(page, '08-image-blanks')
  await poster.getByRole('button', { name: '이 디자인으로 시작' }).click()

  // The sentence arrives filled in — and still editable, which is the whole
  // reason it goes to the composer rather than straight to the model.
  const composer = page.getByLabel('프롬프트 입력')
  await expect(composer).toHaveValue(/연구실 개방 행사/)
  await expect(composer).toHaveValue(/밝고 활기찬/)
  await expect(composer).not.toHaveValue(/\{/)
  // Picking a shape and then setting its aspect by hand would be asking twice.
  await expect(page.getByRole('button', { name: '비율 9:16' })).toBeVisible()
  await expect(page.getByRole('button', { name: '스타일 일러스트' })).toBeVisible()

  // ── video: the same, plus the settings that surface has ─────────────
  await page.goto('/new/av')
  const avGallery = await openGallery(page, ['video-product', 'video-opening'])
  const opener = avGallery.locator('div.group', { hasText: '발표 오프닝' })
  await expect(opener).toBeVisible({ timeout: 20_000 })
  await opener.getByLabel('움직임').selectOption('가볍게 떠다니는 입자')
  await shot(page, '09-video-blanks')
  await opener.getByRole('button', { name: '이 디자인으로 시작' }).click()

  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(/가볍게 떠다니는 입자/)
  await expect(page.getByRole('button', { name: '해상도 1080p' })).toBeVisible()
  await expect(page.getByRole('button', { name: '종류 영상' })).toBeVisible()
  await expect(page.getByText('발표 오프닝', { exact: true })).toBeVisible()

  // ── audio: picking one switches the surface's mode ──────────────────
  const audioGallery = await openGallery(page, ['audio-narration', 'audio-bed'])
  const bed = audioGallery.locator('div.group', { hasText: '배경 음악' })
  await bed.getByRole('button', { name: '이 디자인으로 시작' }).click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(/잔잔하고 따뜻한/)
  // A music template on the video mode would generate the wrong thing.
  await expect(page.getByRole('button', { name: '유형 음악' })).toBeVisible()
  await expect(page.getByRole('button', { name: '종류 오디오' })).toBeVisible()
})

test('이미지 디자인은 프롬프트를 다듬을 뿐 세션의 템플릿이 되지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  await page.goto('/new/image')
  const gallery = await openGallery(page, ['image-poster', 'image-cover'])
  const card = gallery.locator('div.group', { hasText: '포스터' })
  await expect(card).toBeVisible({ timeout: 20_000 })

  // The card for an image template shows its recipe rather than a picture:
  // the result comes from the model and the project's design system, so a
  // sample image would advertise something this template cannot promise.
  const previewUrl = await card.locator('iframe').getAttribute('src')
  const preview = await page.request.get(previewUrl!)
  expect(await preview.text()).toContain('글자를 그리지 않음')

  await shot(page, '06-image-gallery')
  await card.getByRole('button', { name: '이 디자인으로 시작' }).click()
  await expect(page.getByLabel('프롬프트 입력')).not.toHaveValue('')
  await expect(page.getByText('포스터', { exact: true })).toBeVisible()
})

test('디자인을 고르지 않으면 슬라이드는 그대로 JSON 덱으로 나온다', async ({ page }) => {
  test.setTimeout(600_000)
  await signIn(page)

  // The regression this whole track had to avoid: the built-in path is only
  // replaced when somebody picks a shape.
  await page.goto('/new/slides')
  await expect(page.getByRole('button', { name: '디자인 고르기' })).toBeVisible()
  await page.getByLabel('프롬프트 입력').fill('사무실 보안 수칙을 알리는 짧은 발표 자료')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]

  await expect(page.getByRole('button', { name: '내보내기', exact: true })).toBeEnabled({
    timeout: 480_000,
  })
  await shot(page, '07-builtin-deck-unchanged')
  const stored = await artifactOf(page, sessionId)
  expect(stored.kind).toBe('deck')
  expect(stored.data.slides.length).toBeGreaterThanOrEqual(5)
})
