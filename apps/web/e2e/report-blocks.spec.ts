/** The kpi, steps and mermaid blocks survive every reader: web view, page editor, exports.
 *  Tiptap drops nodes its schema lacks, so the page editor is where a block goes missing. Seeded via the API. */
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

/** The report this file seeds; titles are not unique, so it is held by id. */
let seeded: { id: string; sectionId: string } | null = null

/** The chart in either form: mermaid's `<svg>`, or the `<img>` it becomes once the picture is stored. */
const FIGURE = 'svg[id^="d"], figure img, img[src^="data:image/png"]'

/** The figure inside the seeded section; other sections carry other runs' diagrams. */
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

/** Puts `BODY` into the first section of the report that opens. */
async function seed(page: Page) {
  seeded = await openAndSeedReport(page, BODY)
}

/** Enters the in-place editor: 문서 수정, or 내용 편집 when the document opened on its pages. */
async function enterEdit(page: Page) {
  const edit = page.getByRole('button', { name: '문서 수정' })
  if (await edit.isVisible().catch(() => false)) await edit.click()
  else await page.getByRole('button', { name: '내용 편집' }).click()
  await expect(page.locator('.page').first()).toBeVisible({ timeout: 30_000 })
}

test('웹뷰에서 세 블록이 그려지고 펜스는 남지 않는다', async ({ page }) => {
  await seed(page)
  await expect(page.getByText('오탐 감소').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('중복을 걸러낸다').first()).toBeVisible()
  await expect(seededFigure(page)).toBeVisible({ timeout: 20_000 })
  // No fence left in the document itself (the transcript and gallery card may show source).
  const rendered = (await page.locator('section[id^="sec-"]').allInnerTexts()).join('\n')
  for (const opener of ['```kpi', '```steps', '```mermaid']) {
    expect(rendered, `${opener} 가 본문에 그대로 남았다`).not.toContain(opener)
  }
})

test('페이지뷰에 같은 세 블록이 서식의 마크업으로 들어 있다', async ({ page }) => {
  await seed(page)
  await enterEdit(page)

  // The seeded section is the first one.
  const strip = page.locator('.page .kpi').first()
  await expect(strip).toBeVisible({ timeout: 15_000 })
  await expect(strip.locator('> div')).toHaveCount(3)
  await expect(strip).toContainText('99.2%')

  const steps = page.locator('.page ol.steps').first()
  await expect(steps).toBeVisible()
  await expect(steps.locator('> li')).toHaveCount(3)
  await expect(steps).toContainText('세 기준으로 견준다')

  // The 서식 owns the sizes; step numbers are drawn by CSS, not typed.
  const measured = await page.locator('.page').first().evaluate((el) => {
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
  // An edit stores the whole section as HTML; `richtext` turns the markup back into fences.
  await seed(page)
  await enterEdit(page)
  await expect(page.locator('.page .kpi').first()).toBeVisible({ timeout: 30_000 })

  await page.locator('.page .ProseMirror p').first().click()
  await page.keyboard.type(' 확인.')
  await page.getByRole('button', { name: /저장/ }).first().click()
  await page.waitForTimeout(1500)

  await expect(page.locator('.page .kpi').first().locator('> div')).toHaveCount(3)
  await expect(page.locator('.page ol.steps').first().locator('> li')).toHaveCount(3)
})

test('페이지뷰에서 고쳐도 도해가 지워지지 않는다', async ({ page }) => {
  await seed(page)
  // The web view draws the chart and stores the picture before the page view can carry it.
  await expect(seededFigure(page)).toBeVisible({ timeout: 20_000 })
  await expect.poll(() => storedDiagrams(page), { timeout: 25_000 }).toBeGreaterThan(0)

  await enterEdit(page)
  // Named by its source: other sections carry other runs' diagrams.
  const figure = page.locator('.page figure.diagram[data-source*="pie showData"]')
  await expect(figure).toHaveCount(1)

  await page.locator('.page .ProseMirror p').first().click()
  await page.keyboard.type(' 확인.')
  await page.getByRole('button', { name: /저장/ }).first().click()
  await page.waitForTimeout(2000)

  await expect(figure).toHaveCount(1)
  // Back in the web view the source came back as a fence, so it is still a diagram.
  await enterEdit(page)
  await expect(seededFigure(page)).toBeVisible({ timeout: 20_000 })
})

test('브라우저가 그린 차트가 내려받은 파일에 들어 있다', async ({ page }) => {
  // Mermaid runs only in the browser: the page rasterises the chart and posts it back;
  // the exporters find it under the digest of the same source.
  await seed(page)
  await expect(seededFigure(page)).toBeVisible({ timeout: 20_000 })
  // Wait for the picture to reach the server.
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
        // A `.docx` names its pictures; a PDF carries them as image XObjects.
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
  // A reader who goes straight to the page view must not see a placeholder.
  await seed(page)
  await enterEdit(page)

  const figure = page.locator('.page figure.diagram[data-source*="pie showData"]')
  await expect(figure).toHaveCount(1)
  // Drawn off-screen and shown as the picture.
  await expect(figure.locator('img')).toBeVisible({ timeout: 25_000 })
  await expect(figure.locator('img')).toHaveAttribute('src', /^data:image\/png/)
})
