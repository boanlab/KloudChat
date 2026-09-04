import { readFile } from 'node:fs/promises'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { approvePlan, artifactReady, ribbonTab, signIn, surfaceOn } from './helpers'

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

/** Design templates (서식) from the card to the exported file: the HTML document path.
 *  One deck and one document generated per run; image/av templates are exercised only up to the pick. */

/** A named screenshot under `test-results/shots/`. */
async function shot(page: Page, name: string) {
  await page.screenshot({ path: `test-results/shots/${name}.png`, fullPage: false })
}

/** Finds a card by searching, since the gallery pages its grid. */
async function findCard(dialog: Locator, name: string) {
  const search = dialog.getByLabel(/서식 검색|시작점 검색/)
  if (await search.count()) await search.fill(name)
  const found = dialog.locator('div.group', { hasText: name })
  await expect(found.first()).toBeVisible({ timeout: 20_000 })
  return found.first()
}

/** Opens the gallery and waits for the preview responses (the frames are `sandbox=""`, so the paint is unobservable). */
async function openGallery(page: Page, ids: string[]) {
  const previews = Promise.all(
    ids.map((id) =>
      page.waitForResponse((r) => r.url().includes(`/design-templates/${id}/preview`), {
        timeout: 20_000,
      }),
    ),
  )
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  // A second open in one test serves previews from cache, with no response.
  await previews.catch(() => undefined)
  // Settle for the screenshot.
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
      // Listing rows are partial; the document is a fetch by id.
      return row ? await eval(fn)('/api/artifacts/' + row.id) : null
    },
    [AS_USER, sessionId],
  )
}

test('카탈로그는 홈과 작업 중 화면 양쪽에서 닿는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  // One dialogue per surface.
  await page.goto('/new/slides')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  const gallery = page.getByRole('dialog')
  await expect(gallery).toBeVisible({ timeout: 20_000 })
  await shot(page, '13-work-start')

  // The composer stays empty; the chip names the 서식.
  await gallery
    .locator('div')
    .filter({ hasText: '편집형 덱' })
    .last()
    .getByRole('button', { name: '이 서식으로 시작' })
    .click()
  await expect(page).toHaveURL(/\/new\/slides/, { timeout: 20_000 })
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
  // Named once, in the composer.
  await expect(page.getByText('편집형 덱', { exact: true })).toHaveCount(1)
  await expect(page.getByRole('button', { name: '편집형 덱 서식 해제' })).toBeVisible()

  await page.goto('/designs')
  await expect(page.getByRole('region', { name: '디자인 시스템' })).toBeVisible({
    timeout: 20_000,
  })

  // One entry point before the first turn.
  await page.goto('/new/report')
  await expect(page.getByRole('button', { name: '작업 시작하기' })).toHaveCount(1)

  // 디자인 is the design system's word alone.
  await expect(page.getByRole('button', { name: /디자인/ })).toHaveCount(0)
})

test('덱 서식을 고르면 그 템플릿의 HTML 이 나오고 파일로 받을 수 있다', async ({ page }) => {
  test.setTimeout(600_000)
  await signIn(page)

  await page.goto('/new/slides')
  const gallery = await openGallery(page, ['deck-editorial', 'deck-signal'])
  // The 서식 is chosen on the starting point's card (`design-catalogue.spec.ts` covers 서식 cards).
  const job = gallery.locator('div.group').first()
  await job.getByRole('button', { name: /결과 모양 고르기/ }).click()
  await page.getByRole('menuitem', { name: '편집형 덱' }).click()
  await expect(job.getByRole('button', { name: /결과 모양 고르기/ })).toHaveText(/편집형 덱/)

  await shot(page, '01-deck-gallery')

  await job.getByRole('button', { name: /시작점 선택/ }).click()
  await expect(gallery).toBeHidden()
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
  // Named once, by the chip.
  await expect(page.getByText('편집형 덱', { exact: true })).toHaveCount(1)
  await expect(page.getByRole('button', { name: '편집형 덱 서식 해제' })).toBeVisible()
  await shot(page, '02-deck-picked')

  await page.getByLabel('프롬프트 입력').fill('연구실 장비 점검 결과를 발표할 자료를 만들어줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]

  // Nothing is written until the plan is approved; the stop control, not the export button, is the finish line.
  await approvePlan(page, 480_000)
  await artifactReady(page)

  await shot(page, '03-deck-rendered')

  const stored = await artifactOf(page, sessionId)
  expect(stored, '이 세션의 아티팩트가 없습니다').not.toBeNull()
  expect(stored.kind).toBe('html')
  expect(stored.data.templateId).toBe('deck-editorial')
  expect(stored.data.blocks.length).toBeGreaterThanOrEqual(4)
  // An empty array means the linter ran; absent means it did not.
  expect(Array.isArray(stored.data.lint)).toBe(true)
  expect(stored.data.blocks[0].layout).toBe('cover')

  const html = stored.data.content as string
  expect(html).toContain('<section class="slide cover">')
  expect(html).toContain('@media print')
  // The model writes content only: no script.
  expect(html.toLowerCase()).not.toContain('<script')
  // Every block sits inside a styled section.
  expect((html.match(/<section class="slide/g) ?? []).length).toBeGreaterThanOrEqual(4)

  await ribbonTab(page, '파일')
  const exportButton = page.getByRole('button', { name: '내보내기', exact: true })
  const savedHtml = page.waitForEvent('download', { timeout: 60_000 })
  await exportButton.click()
  await page.getByRole('menuitem', { name: '원본 HTML' }).click()
  const htmlFile = await savedHtml
  expect(htmlFile.suggestedFilename()).toMatch(/\.html$/)
  expect(await readFile(await htmlFile.path(), 'utf8')).toContain(
    '<section class="slide cover">',
  )

  // PowerPoint: one slide part per section (`page_export`).
  const savedPptx = page.waitForEvent('download', { timeout: 60_000 })
  await exportButton.click()
  await page.getByRole('menuitem', { name: 'PowerPoint' }).click()
  const pptxFile = await savedPptx
  expect(pptxFile.suggestedFilename()).toMatch(/\.pptx$/)
  const parts = zipNames(await readFile(await pptxFile.path()))
  expect(parts.filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n))).toHaveLength(
    stored.data.blocks.length,
  )

  // Rewrite one block: the part is chosen from the plan, since the preview is sandboxed.
  await page.getByRole('button', { name: '다시 쓰기', exact: true }).click()
  const second = stored.data.blocks[1].title as string
  await page.getByRole('menuitem', { name: second }).click()
  await page.getByLabel('고칠 내용').fill('항목을 세 줄로 줄이고, 숫자는 빼 주세요.')
  await page.getByRole('dialog').getByRole('button', { name: '다시 쓰기' }).click()
  await expect(page.getByRole('dialog')).toBeHidden({ timeout: 240_000 })

  const rewritten = await artifactOf(page, sessionId)
  expect(rewritten.version).toBe(stored.version + 1)
  expect(rewritten.data.blocks[1].html).not.toBe(stored.data.blocks[1].html)
  // Neighbours untouched; file rebuilt from the same seed.
  expect(rewritten.data.blocks[0].html).toBe(stored.data.blocks[0].html)
  expect(rewritten.data.content).toContain('<section class="slide cover">')
  expect(rewritten.data.content).toContain(rewritten.data.blocks[1].html.slice(0, 30))

  // The catalogue is still reachable after the first turn. Close the panel first:
  // below the desktop breakpoint it overlays the composer.
  const panel = page.locator('aside[data-panel="artifact"]')
  await panel.getByRole('button', { name: '닫기' }).click()
  await expect(panel).toBeHidden()

  await expect(page.getByRole('button', { name: '작업 시작하기' })).toHaveCount(1)
  await page.getByRole('button', { name: '작업 시작하기' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: '닫기' }).click()

  // Taking the shape off clears the session row, not only the chip.
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

  // The gallery card names the template the document was written into, not the kind.
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
  const job = gallery.locator('div.group').first()
  await job.getByRole('button', { name: /결과 모양 고르기/ }).click()
  await page.getByRole('menuitem', { name: '한 장 요약' }).click()
  await shot(page, '04-document-gallery')
  await job.getByRole('button', { name: /시작점 선택/ }).click()

  await page.getByLabel('프롬프트 입력').fill('학과 서버를 교체할지 정하는 한 장 요약을 써줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]

  await approvePlan(page, 480_000)
  await artifactReady(page)

  await shot(page, '05-document-rendered')

  const stored = await artifactOf(page, sessionId)
  expect(stored.data.templateId).toBe('doc-brief')

  // A 서식 document is a report whose blocks are `format: "html"` sections; `html` artifacts are decks.
  const sections = stored.data.sections as { content: string; format: string }[]
  expect(sections.length).toBeGreaterThan(0)
  expect(sections.every((s) => s.format === 'html')).toBe(true)

  const html = sections.map((s) => s.content).join('\n')
  // The 서식's own markup.
  expect(html).toContain('<section>')
  expect(html).toMatch(/<h[23]>/)
  // Not a deck.
  expect(html).not.toContain('class="slide')
  // The cover is drawn by the page view, not stored as a section.
  expect(html).not.toContain('class="cover"')
})

test('이미지·영상 템플릿은 빈칸을 채워 문장을 완성하고 옵션까지 맞춰 준다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
  const imageGallery = await openGallery(page, ['image-poster', 'image-cover'])
  const poster = await findCard(imageGallery, '포스터')
  await expect(poster).toBeVisible({ timeout: 20_000 })

  // The card lists its questions; the composer asks them.
  await expect(poster.getByText('무엇을')).toBeVisible()
  await expect(poster.getByRole('textbox')).toHaveCount(0)
  await poster.getByRole('button', { name: '이 서식으로 시작' }).click()

  // Questions above the box, each blank with its example; usable without typing.
  const imageQuestions = page.getByRole('group', { name: '포스터 시작점 질문' })
  await expect(imageQuestions.getByLabel('포스터 · 무엇을')).toHaveAttribute('placeholder', '학과 연구 성과 발표회')
  await imageQuestions.getByLabel('포스터 · 무엇을').fill('연구실 개방 행사')
  await imageQuestions.getByLabel('포스터 · 분위기').selectOption('밝고 활기찬')
  await shot(page, '08-image-blanks')
  // The chips follow the 서식.
  await expect(page.getByRole('button', { name: '비율 9:16' })).toBeVisible()
  await expect(page.getByRole('button', { name: '스타일 일러스트' })).toBeVisible()
  // Sent as a sentence: filled values, examples for empty blanks.
  let asked = ''
  await page.route('**/api/sessions/*/images', async (route) => {
    asked = (route.request().postDataJSON() as { prompt: string }).prompt
    await route.fulfill({ json: [] })
  })
  await page.getByRole('button', { name: '전송' }).click()
  await expect.poll(() => asked, { timeout: 30_000 }).toMatch(/연구실 개방 행사/)
  expect(asked).toMatch(/밝고 활기찬/)
  expect(asked).not.toMatch(/\{/)

  test.skip(!(await surfaceOn(page, 'av')), 'av 표면이 꺼져 있습니다')
  const avGallery = await openGallery(page, ['video-product', 'video-opening'])
  const opener = await findCard(avGallery, '발표 오프닝')
  await expect(opener).toBeVisible({ timeout: 20_000 })
  await opener.getByRole('button', { name: '이 서식으로 시작' }).click()
  const videoQuestions = page.getByRole('group', { name: '발표 오프닝 시작점 질문' })
  await videoQuestions.getByLabel('발표 오프닝 · 움직임').selectOption('가볍게 떠다니는 입자')
  await shot(page, '09-video-blanks')
  await expect(page.getByRole('button', { name: '해상도 1080p' })).toBeVisible()
  await expect(page.getByRole('button', { name: '종류 영상' })).toBeVisible()

  const audioGallery = await openGallery(page, ['audio-narration', 'audio-bed'])
  const bed = await findCard(audioGallery, '배경 음악')
  await bed.getByRole('button', { name: '이 서식으로 시작' }).click()
  await expect(page.getByRole('group', { name: '배경 음악 시작점 질문' })).toBeVisible()
  // The template switches the surface's mode.
  await expect(page.getByRole('button', { name: '유형 음악' })).toBeVisible()
  await expect(page.getByRole('button', { name: '종류 오디오' })).toBeVisible()
})

test('이미지 서식은 프롬프트를 다듬을 뿐 세션의 템플릿이 되지 않는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  test.skip(!(await surfaceOn(page, 'image')), 'image 표면이 꺼져 있습니다')
  const gallery = await openGallery(page, ['image-poster', 'image-cover'])
  const card = await findCard(gallery, '포스터')
  await expect(card).toBeVisible({ timeout: 20_000 })

  // An image template's card shows its recipe, in the catalogue's own words.
  await expect(card).toContainText('글자는 넣지 않고')

  await shot(page, '06-image-gallery')
  await card.getByRole('button', { name: '이 서식으로 시작' }).click()
  // Questions open above the box; nothing lands in it.
  await expect(page.getByLabel('프롬프트 입력')).toHaveValue('')
  await expect(page.getByRole('group', { name: '포스터 시작점 질문' })).toBeVisible()
  await expect(page.getByRole('button', { name: '포스터 서식 해제' })).toBeVisible()
})

test('서식을 고르지 않으면 슬라이드는 그대로 JSON 덱으로 나온다', async ({ page }) => {
  test.setTimeout(600_000)
  await signIn(page)

  // The built-in path is replaced only when a shape is picked.
  await page.goto('/new/slides')
  await expect(page.getByRole('button', { name: '작업 시작하기' })).toBeVisible()
  await page.getByLabel('프롬프트 입력').fill('사무실 보안 수칙을 알리는 짧은 발표 자료')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]
  await approvePlan(page, 480_000)

  await artifactReady(page)
  await shot(page, '07-builtin-deck-unchanged')
  const stored = await artifactOf(page, sessionId)
  expect(stored.kind).toBe('deck')
  expect(stored.data.slides.length).toBeGreaterThanOrEqual(5)
})

test('쪽을 넘겨도 대화상자 크기가 그대로다', async ({ page }) => {
  // Card heights differ per page; the dialog must not resize under the buttons.
  await signIn(page)
  await page.goto('/new/report')
  await page.getByRole('button', { name: '작업 시작하기' }).click()
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
