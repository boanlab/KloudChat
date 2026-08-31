/**
 * The three structured blocks, read back everywhere they are read.
 *
 * A strip of figures, a numbered procedure and a chart each have four readers
 * — the web view, the page view, the export writer and the section editor —
 * and three of them are different code. The failure this guards against is the
 * one the table had: visible in the web view, and three lines of backticks the
 * moment somebody switched to pages.
 *
 * The page view is where it actually breaks. Tiptap parses the document into
 * its own schema and drops every node the schema has no entry for, so a block
 * can be missing from the page view while looking perfect in the web view and
 * correct in the exported file. Nothing but opening the page view catches it.
 *
 * Seeded through the API rather than typed, so the test says something about
 * the blocks rather than about the editor's buttons, and so it costs no
 * generation.
 */
import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, openAndSeedReport } from './helpers'

const BODY = [
  '앞 문장.',
  '',
  '```kpi',
  '32% | 오탐 감소',
  '1.4초 | 평균 응답',
  '99.2% | 가용성',
  '```',
  '',
  '```steps',
  '수집 | 자료를 모은다',
  '정제 | 중복을 걸러낸다',
  '분석 | 세 기준으로 견준다',
  '```',
  '',
  '```mermaid',
  'pie showData',
  '    title 예산 구성',
  '    "인건비" : 52',
  '    "장비" : 28',
  '    "운영" : 20',
  '```',
  '',
  '뒤 문장.',
].join('\n')

/**
 * The report this file seeds, remembered between the seeding and the reading.
 *
 * "Whichever report comes back first" was being resolved separately by every
 * helper here, and five reports in the shared account carry the same title, so
 * a case could seed one document, open a second and count the pictures on a
 * third. Held as an id from the moment the seed picks one.
 */
let seeded: { id: string; sectionId: string } | null = null

/**
 * The chart, in either form, inside the section this file seeds.
 *
 * Two forms because both are correct: mermaid draws an `<svg>`, and the moment
 * the picture is stored and the store refreshes it becomes an `<img>` — which
 * is the storing working. A test that insists on the SVG is asserting that
 * nobody has opened this document before, and the second run of the day is
 * enough to make that false.
 *
 * Scoped to the section because the document is a shared scratch report whose
 * other sections carry other runs' diagrams, and an unscoped `.first()` was
 * resolving to one of those — hidden, or drawn from a source nobody here
 * wrote.
 */
const FIGURE = 'svg[id^="d"], figure img, img[src^="data:image/png"]'

function seededFigure(page: Page) {
  return page.locator(`#sec-${seeded!.sectionId}`).locator(FIGURE).first()
}

/** How many diagram pictures the server is holding for the seeded report. */
async function storedDiagrams(page: Page): Promise<number> {
  return page.evaluate(async (admin) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: admin.email, password: admin.password }),
    })
    const session = await login.json()
    const headers = { Authorization: `Bearer ${session.accessToken ?? session.access_token}` }
    const full = await (
      await fetch(`/api/artifacts/${admin.id}`, { headers, credentials: 'include' })
    ).json()
    const sections = (full.data ?? full).sections as { diagrams?: Record<string, string> }[]
    return sections.reduce((n, s) => n + Object.keys(s.diagrams ?? {}).length, 0)
  }, { ...E2E_ADMIN, id: seeded!.id })
}

/** Puts `BODY` into the first section of the report that opens, and opens it. */
async function seed(page: Page) {
  seeded = await openAndSeedReport(page, BODY)
}

test('웹뷰에서 세 블록이 그려지고 펜스는 남지 않는다', async ({ page }) => {
  await seed(page)
  await expect(page.getByText('오탐 감소').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('중복을 걸러낸다').first()).toBeVisible()
  // The chart is drawn by mermaid, so it arrives as an SVG rather than as text.
  await expect(seededFigure(page)).toBeVisible({ timeout: 20_000 })
  // No fence left in the rendered prose. Asked of the document rather than of
  // the page, and it took two goes to get that right: `body.innerText()`
  // covers the sidebar and the transcript, where somebody's own message may
  // legitimately contain a code fence, and the gallery card behind the panel
  // shows the artifact's Markdown source — which is what that card is for.
  // The claim is about what a reader of the document sees.
  const rendered = (await page.locator('section[id^="sec-"]').allInnerTexts()).join('\n')
  for (const opener of ['```kpi', '```steps', '```mermaid']) {
    expect(rendered, `${opener} 가 본문에 그대로 남았다`).not.toContain(opener)
  }
})

test('페이지뷰에 같은 세 블록이 서식의 마크업으로 들어 있다', async ({ page }) => {
  await seed(page)
  await page.getByRole('button', { name: '페이지뷰' }).click()
  await expect(page.locator('.page')).toBeVisible({ timeout: 30_000 })

  // The seeded section is the first one, and the document around it is a
  // shared scratch report that other runs have left their own strips in.
  const strip = page.locator('.page .kpi').first()
  await expect(strip).toBeVisible({ timeout: 15_000 })
  await expect(strip.locator('> div')).toHaveCount(3)
  await expect(strip).toContainText('99.2%')

  const steps = page.locator('.page ol.steps').first()
  await expect(steps).toBeVisible()
  await expect(steps.locator('> li')).toHaveCount(3)
  await expect(steps).toContainText('세 기준으로 견준다')

  // The 서식 owns the sizes, and the step numbers are drawn by CSS rather than
  // typed into the text — otherwise deleting a step leaves the rest misnumbered.
  const measured = await page.locator('.page').evaluate((el) => {
    const size = (s: string) =>
      parseFloat(getComputedStyle(el.querySelector(s) as HTMLElement).fontSize)
    return {
      value: size('.kpi strong'),
      label: size('.kpi span'),
      firstStep: (el.querySelector('ol.steps > li') as HTMLElement).textContent ?? '',
    }
  })
  expect(measured.value).toBeGreaterThan(measured.label * 1.5)
  expect(measured.firstStep).not.toMatch(/^\s*1[.)]/)
})

test('페이지뷰에서 다른 곳을 고쳐도 블록이 살아남는다', async ({ page }) => {
  // The round trip that loses them. An edit anywhere in a section stores the
  // whole body as HTML, so everything Tiptap could not parse is gone by then —
  // and `richtext` has to turn the markup back into fences for the exporters.
  await seed(page)
  await page.getByRole('button', { name: '페이지뷰' }).click()
  await expect(page.locator('.page .kpi').first()).toBeVisible({ timeout: 30_000 })

  await page.locator('.page .ProseMirror p').first().click()
  await page.keyboard.type(' 확인.')
  await page.getByRole('button', { name: /저장/ }).first().click()
  await page.waitForTimeout(1500)

  await expect(page.locator('.page .kpi').first().locator('> div')).toHaveCount(3)
  await expect(page.locator('.page ol.steps').first().locator('> li')).toHaveCount(3)
})

test('페이지뷰에서 고쳐도 도해가 지워지지 않는다', async ({ page }) => {
  // Found by exporting after an edit and finding no picture in the file. A
  // mermaid fence had no node in the page-view editor, so one keystroke
  // anywhere in the section deleted every diagram and chart in it — from the
  // document, from the web view and from the export — and nothing said so.
  await seed(page)
  // The web view draws the chart and stores the picture; that has to have
  // happened before the page view can carry it.
  // Either freshly drawn or the picture stored by an earlier reader — the
  // component shows the stored one without running mermaid, which is the whole
  // point of storing it, so a test that insists on an SVG is asserting that
  // nobody has opened this document before.
  await expect(seededFigure(page)).toBeVisible({ timeout: 20_000 })
  await expect.poll(() => storedDiagrams(page), { timeout: 25_000 }).toBeGreaterThan(0)

  await page.getByRole('button', { name: '페이지뷰' }).click()
  await expect(page.locator('.page')).toBeVisible({ timeout: 30_000 })
  // Named by its source rather than by being the only one on the page. The
  // document is a shared scratch report and other runs leave their diagrams in
  // its other sections, so "exactly one figure" was asserting something about
  // the fixture rather than about this seed. The source is what tells them
  // apart — and that it travels with the figure at all is the part a picture
  // alone would lose.
  const figure = page.locator('.page figure.diagram[data-source*="pie showData"]')
  await expect(figure).toHaveCount(1)

  await page.locator('.page .ProseMirror p').first().click()
  await page.keyboard.type(' 확인.')
  await page.getByRole('button', { name: /저장/ }).first().click()
  await page.waitForTimeout(2000)

  await expect(figure).toHaveCount(1)
  // And back in the web view it is still a diagram, not a flattened picture:
  // the source came back as a fence, so it can still be changed. The same
  // button goes both ways — its label is the destination, its text is not.
  await page.getByRole('button', { name: '페이지뷰' }).click()
  await expect(seededFigure(page)).toBeVisible({ timeout: 20_000 })
})

test('브라우저가 그린 차트가 내려받은 파일에 들어 있다', async ({ page }) => {
  // The one link the API tests cannot check. Mermaid runs in the browser and
  // nowhere else, so a chart reaches a file only if the page rasterised what
  // it drew and posted it back — and the exporters then found it under the
  // digest of the same source. A break anywhere along that chain shows up as a
  // report that looks right on screen and has a hole in it on paper.
  await seed(page)
  await expect(seededFigure(page)).toBeVisible({ timeout: 20_000 })
  // Waited for rather than slept through, and waited for the thing that
  // actually matters: the picture reaching the server. Drawing is one step and
  // storing is another, and a fixed pause is long enough right up until the
  // suite is busier than it was the day it was written.
  await expect
    .poll(() => storedDiagrams(page), { timeout: 25_000 })
    .toBeGreaterThan(0)

  const files = await page.evaluate(async (admin) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: admin.email, password: admin.password }),
    })
    const session = await login.json()
    const headers = { Authorization: `Bearer ${session.accessToken ?? session.access_token}` }
    const id = admin.id
    const out: Record<string, { bytes: number; picture: boolean }> = {}
    for (const format of ['docx', 'pdf']) {
      const res = await fetch(`/api/artifacts/${id}/export?format=${format}`, {
        headers,
        credentials: 'include',
      })
      const buffer = new Uint8Array(await res.arrayBuffer())
      const raw = new TextDecoder('latin1').decode(buffer)
      out[format] = {
        bytes: buffer.length,
        // A `.docx` is a zip and names its pictures; a PDF carries them as
        // image XObjects.
        picture: format === 'docx' ? /word\/media\//.test(raw) : /\/Subtype\s*\/Image/.test(raw),
      }
    }
    return out
  }, { ...E2E_ADMIN, id: seeded!.id })

  expect(files.docx.picture, 'docx 에 차트 그림이 없다').toBe(true)
  expect(files.pdf.picture, 'pdf 에 차트 그림이 없다').toBe(true)
  expect(files.docx.bytes).toBeGreaterThan(10_000)
})

test('페이지뷰가 아직 없는 도해를 스스로 그린다', async ({ page }) => {
  // Looking the picture up was not enough. It only existed if somebody had
  // already opened the web view — so a reader who went straight to the page
  // view saw a dashed placeholder where a figure belonged, and so did one who
  // watched the document being written and then switched, because this screen
  // holds the copy of the artifact it was handed.
  await seed(page)
  await page.evaluate(() => undefined)
  await page.getByRole('button', { name: '페이지뷰' }).click()
  await expect(page.locator('.page')).toBeVisible({ timeout: 30_000 })

  const figure = page.locator('.page figure.diagram[data-source*="pie showData"]')
  await expect(figure).toHaveCount(1)
  // Drawn off-screen and shown as the picture, not left as a placeholder.
  await expect(figure.locator('img')).toBeVisible({ timeout: 25_000 })
  await expect(figure.locator('img')).toHaveAttribute('src', /^data:image\/png/)
})
