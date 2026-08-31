import { readFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'
import { approvePlan, signIn, surfaceOn } from './helpers'

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
  await page.getByRole('button', { name: '서식 고르기' }).click()
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
      const row = list.find((a: { sessionId: string }) => a.sessionId === id)
      // The listing carries cards — a deck's first four slide titles, an HTML
      // document with no markup at all. The document itself is a fetch by id.
      return row ? await eval(fn)('/api/artifacts/' + row.id) : null
    },
    [AS_USER, sessionId],
  )
}

test('카탈로그는 홈과 작업 중 화면 양쪽에서 닿는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // ── the home screen ─────────────────────────────────────────────────
  // It was reachable only from the empty state of a new session, behind a
  // secondary button — invisible to anybody who did not know it was there.
  await page.goto('/')
  const rail = page.getByRole('region', { name: '서식에서 시작' })
  await expect(rail).toBeVisible({ timeout: 20_000 })
  await expect(rail.getByRole('button').first()).toBeVisible()
  await shot(page, '13-home-rail')

  // Picking one opens its surface wearing the shape. The composer stays empty
  // — the chip names the 서식, and the example sentence was the same borrowed
  // voice the 시작점 chip was built to stop.
  await rail.getByRole('button', { name: /편집형 덱/ }).click()
  await expect(page).toHaveURL(/\/new\/slides/, { timeout: 20_000 })
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
  // Once, and in the composer. "이 대화가 가지고 시작하는 것" listed the 서식
  // too for a while, which printed the same two words at both ends of an empty
  // screen — and only the composer's copy carries the × that undoes the pick.
  await expect(page.getByText('편집형 덱', { exact: true })).toHaveCount(1)
  await expect(page.getByRole('button', { name: '편집형 덱 서식 해제' })).toBeVisible()

  // ── a workspace entry of its own ────────────────────────────────────
  // A design system is a thing you keep and attach, not a preference.
  await page.goto('/designs')
  await expect(page.getByRole('region', { name: '디자인 시스템' })).toBeVisible({
    timeout: 20_000,
  })

  // Before the first turn the empty screen offers it, and the composer does
  // not: two copies of the same button two inches apart is clutter. The
  // composer's copy — the one that survives that turn — is checked in the deck
  // test below, where a conversation actually exists.
  await page.goto('/new/report')
  await expect(page.getByRole('button', { name: '서식 고르기' })).toHaveCount(1)

  // 디자인 belongs to the design system alone. While the catalogue wore the
  // same word, whichever of the two you picked you believed you had picked
  // the other one.
  await expect(page.getByRole('button', { name: /디자인/ })).toHaveCount(0)
})

test('덱 서식을 고르면 그 템플릿의 HTML 이 나오고 파일로 받을 수 있다', async ({ page }) => {
  test.setTimeout(600_000)
  await signIn(page)

  // ── 1. The gallery shows each template's own shape ──────────────────
  await page.goto('/new/slides')
  const gallery = await openGallery(page, ['deck-editorial', 'deck-signal'])
  const card = gallery.locator('div.group', { hasText: '편집형 덱' })
  await expect(card).toBeVisible({ timeout: 20_000 })

  // ── the discipline is on the card ───────────────────────────────────
  // A name and one line do not tell two decks apart; what the result will be
  // read against does. Three rules are printed and the rest fold away, so what
  // is asserted is that shape rather than the sentences — the catalogue can
  // rewrite a rule without anybody having to edit this.
  await expect(card.getByText('이 서식이 확인하는 것')).toBeVisible()
  await expect(card.locator('ul[lang="ko"]').first().locator('li')).toHaveCount(3)
  const folded = card.locator('details li').last()
  await expect(folded).toBeHidden()
  await card.locator('details summary').click()
  await expect(folded).toBeVisible()

  // The preview is the seed rendered around its sample, served as a document.
  await shot(page, '01-deck-gallery')

  // What the card carries now that it carries no picture: the rules the
  // finished deck is read against, and the blanks it asks you to bring. The
  // previews went with the seeds — six 서식 drew the same one, so the picture
  // told two of them apart less well than the name did.
  await expect(card.getByText(/확인하는 것 \d+개/)).toBeVisible()
  await expect(card.getByRole('button', { name: /양식 pptx/ })).toBeVisible()

  // ── 2. Picking one names itself and leaves the box alone ────────────
  await card.getByRole('button', { name: '이 서식으로 시작' }).click()
  await expect(gallery).toBeHidden()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
  // Named once, by the chip — the only copy of the name that can take it off.
  await expect(page.getByText('편집형 덱', { exact: true })).toHaveCount(1)
  await expect(page.getByRole('button', { name: '편집형 덱 서식 해제' })).toBeVisible()
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
  // The turn ends on a proposal and writes nothing; the card is what writes.
  await approvePlan(page, 480_000)
  const exportButton = page.getByRole('button', { name: '내보내기', exact: true })
  await expect(exportButton).toBeVisible({ timeout: 20_000 })

  await shot(page, '03-deck-rendered')

  const stored = await artifactOf(page, sessionId)
  expect(stored, '이 세션의 아티팩트가 없습니다').not.toBeNull()
  expect(stored.kind).toBe('html')
  expect(stored.data.templateId).toBe('deck-editorial')
  expect(stored.data.blocks.length).toBeGreaterThanOrEqual(4)
  // Read back before it was stored, on every document — an empty array is the
  // linter having run and found nothing, which is not the same as absent.
  expect(Array.isArray(stored.data.lint)).toBe(true)
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

  // ── 5. One block can be rewritten, and the file is rebuilt ──────────
  // The preview is sandboxed, so the part is chosen from the plan the file was
  // written from rather than by clicking into the document.
  await page.getByRole('button', { name: '다시 쓰기', exact: true }).click()
  const second = stored.data.blocks[1].title as string
  await page.getByRole('menuitem', { name: second }).click()
  await page.getByLabel('고칠 내용').fill('항목을 세 줄로 줄이고, 숫자는 빼 주세요.')
  await page.getByRole('dialog').getByRole('button', { name: '다시 쓰기' }).click()
  await expect(page.getByRole('dialog')).toBeHidden({ timeout: 240_000 })

  const rewritten = await artifactOf(page, sessionId)
  // A new version, so the previous one is one click from being restored.
  expect(rewritten.version).toBe(stored.version + 1)
  expect(rewritten.data.blocks[1].html).not.toBe(stored.data.blocks[1].html)
  // The neighbours are untouched and the file was rebuilt from the same seed.
  expect(rewritten.data.blocks[0].html).toBe(stored.data.blocks[0].html)
  expect(rewritten.data.content).toContain('<section class="slide cover">')
  expect(rewritten.data.content).toContain(rewritten.data.blocks[1].html.slice(0, 30))

  // ── 6. The catalogue is still reachable now the empty screen is gone ─
  // This is the copy that matters after the first turn: without it a shape
  // could be chosen once and never changed.
  //
  // The panel is put away first, because everything from here on is in the
  // composer. Below the desktop breakpoint the panel is not a column beside
  // the conversation but a full-bleed overlay on top of it — the document is
  // the whole screen while you are reading it, and closing it is how anybody
  // gets back to the box. Reaching past it instead only produced a click that
  // waited out its timeout on a button Playwright could see and a reader
  // could not press.
  const panel = page.locator('aside[data-panel="artifact"]')
  await panel.getByRole('button', { name: '닫기' }).click()
  await expect(panel).toBeHidden()

  await expect(page.getByRole('button', { name: '서식 고르기' })).toHaveCount(1)
  await page.getByRole('button', { name: '서식 고르기' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: '닫기' }).click()

  // ── 7. Taking the shape off reaches the row that holds it ───────────
  // The turn made the choice sticky, so a chip that only disappeared locally
  // would leave the next turn writing into a template nobody can see.
  await page.getByRole('button', { name: /편집형 덱 서식 해제/ }).click()
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

  // ── 8. The gallery card says which shape it came out in ─────────────
  // Everything this path produces is one `html` artifact, so a card labelled
  // by kind labels a deck and a one-pager identically. Read after the shape
  // was taken off the session: what the card names is the template the
  // document was written into, not the one the next turn would use.
  await page.goto('/artifacts')
  await page.getByLabel('아티팩트 검색').fill(stored.title)
  const galleryCard = page.locator('div.grid > *', { hasText: stored.title }).first()
  await expect(galleryCard.getByText('편집형 덱', { exact: true })).toBeVisible({
    timeout: 20_000,
  })
  await expect(galleryCard.getByText('HTML', { exact: true })).toHaveCount(0)
  await shot(page, '16-gallery-template-name')
})

test('문서 서식은 문서 조판으로 나온다', async ({ page }) => {
  test.setTimeout(600_000)
  await signIn(page)

  await page.goto('/new/report')
  const gallery = await openGallery(page, ['doc-report', 'doc-brief'])
  const card = gallery.locator('div.group', { hasText: '한 장 요약' })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await shot(page, '04-document-gallery')
  await card.getByRole('button', { name: '이 서식으로 시작' }).click()

  await page.getByLabel('프롬프트 입력').fill('학과 서버를 교체할지 정하는 한 장 요약을 써줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]

  // The turn ends on a proposal and writes nothing; the card is what writes.
  await approvePlan(page, 480_000)
  await expect(page.getByRole('button', { name: '내보내기', exact: true })).toBeVisible({
    timeout: 20_000,
  })

  await shot(page, '05-document-rendered')

  const stored = await artifactOf(page, sessionId)
  expect(stored.data.templateId).toBe('doc-brief')

  // Sections, not one `content` string. A document 서식 and a document with no
  // 서식 used to be different artifacts — one could be read, exported and
  // rewritten a block at a time but never typed into, and the other could be
  // typed into and had no shape. Neither could become the other, so they are
  // one artifact now: the writer's blocks are already HTML, so they are stored
  // as `format: "html"` sections and the page view renders them through
  // whichever 서식 is chosen, including a different one later. The `html`
  // artifact stayed with the deck surface, where a slide is not a section.
  const sections = stored.data.sections as { content: string; format: string }[]
  expect(sections.length).toBeGreaterThan(0)
  expect(sections.every((s) => s.format === 'html')).toBe(true)

  const html = sections.map((s) => s.content).join('\n')
  // The 서식's own markup, not prose that has been through a Markdown writer.
  expect(html).toContain('<section>')
  expect(html).toMatch(/<h[23]>/)
  // A document, not a deck — the slide vocabulary belongs to the other seed.
  expect(html).not.toContain('class="slide')
  // The cover is the 서식's, not a block of the document: it is what the page
  // view draws around the sections, so it is not one of them.
  expect(html).not.toContain('class="cover"')
})

test('이미지·영상 템플릿은 빈칸을 채워 문장을 완성하고 옵션까지 맞춰 준다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  // ── image: blanks become a sentence, and the chips follow ───────────
  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
  const imageGallery = await openGallery(page, ['image-poster', 'image-cover'])
  const poster = imageGallery.locator('div.group', { hasText: '포스터' })
  await expect(poster).toBeVisible({ timeout: 20_000 })

  // Every blank starts filled, so the card is usable without typing.
  await expect(poster.getByLabel('무엇을')).toHaveValue('학과 연구 성과 발표회')
  await poster.getByLabel('무엇을').fill('연구실 개방 행사')
  await poster.getByLabel('분위기').selectOption('밝고 활기찬')
  await shot(page, '08-image-blanks')
  await poster.getByRole('button', { name: '이 서식으로 시작' }).click()

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
  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')
  const avGallery = await openGallery(page, ['video-product', 'video-opening'])
  const opener = avGallery.locator('div.group', { hasText: '발표 오프닝' })
  await expect(opener).toBeVisible({ timeout: 20_000 })
  await opener.getByLabel('움직임').selectOption('가볍게 떠다니는 입자')
  await shot(page, '09-video-blanks')
  await opener.getByRole('button', { name: '이 서식으로 시작' }).click()

  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(/가볍게 떠다니는 입자/)
  await expect(page.getByRole('button', { name: '해상도 1080p' })).toBeVisible()
  await expect(page.getByRole('button', { name: '종류 영상' })).toBeVisible()
  // And no chip afterwards. An a/v 서식 is spent on the sentence and the chips
  // it just set — it has no clause to send with the turn, so a badge saying it
  // is still in force would name a shape nothing carries.
  await expect(page.getByText('발표 오프닝', { exact: true })).toHaveCount(0)

  // ── audio: picking one switches the surface's mode ──────────────────
  const audioGallery = await openGallery(page, ['audio-narration', 'audio-bed'])
  const bed = audioGallery.locator('div.group', { hasText: '배경 음악' })
  await bed.getByRole('button', { name: '이 서식으로 시작' }).click()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue(/잔잔하고 따뜻한/)
  // A music template on the video mode would generate the wrong thing.
  await expect(page.getByRole('button', { name: '유형 음악' })).toBeVisible()
  await expect(page.getByRole('button', { name: '종류 오디오' })).toBeVisible()
})

test('이미지 서식은 프롬프트를 다듬을 뿐 세션의 템플릿이 되지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
  const gallery = await openGallery(page, ['image-poster', 'image-cover'])
  const card = gallery.locator('div.group', { hasText: '포스터' })
  await expect(card).toBeVisible({ timeout: 20_000 })

  // The card for an image template shows its recipe rather than a picture:
  // the result comes from the model and the project's design system, so a
  // sample image would advertise something this template cannot promise.
  //
  // Read off the catalogue's own words. The phrase this asserted before —
  // "글자를 그리지 않음" — was a preview caption, and previews were taken out;
  // the assertion outlived the thing it was describing and failed on a card
  // that says the same thing in the template's own description.
  await expect(card).toContainText('글자는 넣지 않고')

  await shot(page, '06-image-gallery')
  await card.getByRole('button', { name: '이 서식으로 시작' }).click()
  await expect(page.getByLabel('프롬프트 입력')).not.toHaveValue('')
  // Same rule on this surface: the pick is named once, on the chip that clears
  // it, and not a second time by the empty screen above.
  await expect(page.getByText('포스터', { exact: true })).toHaveCount(1)
  await expect(page.getByRole('button', { name: '포스터 서식 해제' })).toBeVisible()
})

test('서식을 고르지 않으면 슬라이드는 그대로 JSON 덱으로 나온다', async ({ page }) => {
  test.setTimeout(600_000)
  await signIn(page)

  // The regression this whole track had to avoid: the built-in path is only
  // replaced when somebody picks a shape.
  await page.goto('/new/slides')
  await expect(page.getByRole('button', { name: '서식 고르기' })).toBeVisible()
  await page.getByLabel('프롬프트 입력').fill('사무실 보안 수칙을 알리는 짧은 발표 자료')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]
  // The built-in path plans and stops like every other one; what this checks is
  // the shape of what it writes, which only exists after the approval.
  await approvePlan(page, 480_000)

  await expect(page.getByRole('button', { name: '내보내기', exact: true })).toBeEnabled({
    timeout: 480_000,
  })
  await shot(page, '07-builtin-deck-unchanged')
  const stored = await artifactOf(page, sessionId)
  expect(stored.kind).toBe('deck')
  expect(stored.data.slides.length).toBeGreaterThanOrEqual(5)
})

test('쪽을 넘겨도 대화상자 크기가 그대로다', async ({ page }) => {
  // 넘길 때마다 상자가 자라고 줄면 닫기 버튼도 쪽 넘김도 매번 다른 자리에
  // 있다. 쪽마다 카드 높이가 다르고 — 서식 카드는 문장 카드의 두 배쯤 된다 —
  // 마지막 쪽은 넷이 아니라 남은 몇 장이므로, 놔두면 상자가 382 와 686 사이를
  // 오르내린다.
  await signIn(page)
  await page.goto('/new/report')
  await page.getByRole('button', { name: '서식 고르기' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog').locator('.grid > *').first()).toBeVisible({
    timeout: 20_000,
  })

  const grid = page.getByRole('dialog').locator('.grid')
  const tall = async () => Math.round((await grid.boundingBox())!.height)

  const pages = Number(
    (await page.getByRole('dialog').getByText(/^\d+ \/ \d+$/).innerText()).split('/')[1],
  )
  expect(pages, '쪽이 하나뿐이면 이 사례는 아무것도 확인하지 못한다').toBeGreaterThan(1)

  const first = await tall()
  for (let i = 1; i < pages; i++) {
    await page.getByRole('button', { name: '다음 쪽' }).click()
    await page.waitForTimeout(400)
    expect(await tall(), `${i + 1}쪽에서 높이가 달라졌다`).toBe(first)
  }
})
