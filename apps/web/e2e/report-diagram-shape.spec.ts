/**
 * The shape a diagram comes out in, and whose colours it is drawn in.
 *
 * Two complaints, one cause. A `graph TD` stacks one rank per row, so a chain
 * of five is a tall narrow picture — and `useMaxWidth` then scales it *down*
 * to the column, which shrinks the text rather than fixing the shape. And a
 * model reaches for `style` constantly, so one figure in a report came out in
 * pink and yellow while everything around it was the 서식's own ink.
 *
 * The prompt says both of these. This is the half that holds when the writer
 * ignores it.
 */
import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, openAndSeedReport } from './helpers'

/**
 * The figure, in either of the two forms it legitimately takes.
 *
 * It is an `<svg>` mermaid has just drawn, and it becomes an `<img>` the
 * moment the picture is stored and the store refreshes — the component
 * working, not failing. `figure img` and `.my-5 img` used to stand in for the
 * second form and matched neither: the stored picture is not inside a
 * `<figure>`, and `my-5` is a class on the `<img>` itself rather than on an
 * ancestor. It went unnoticed for as long as flowcharts never got stored.
 */
const FIGURE = 'svg[id^="d"], figure img, img[src^="data:image/png"]'

/**
 * The same, narrowed to the section this file seeds.
 *
 * The document is a shared scratch report and other runs leave diagrams in its
 * other sections, so `FIGURE` on the whole page resolves to whichever figure
 * comes first in the DOM — another section's, drawn from another source. This
 * file measures shape and colour, and measuring the wrong picture produces a
 * confident number about a diagram nobody here wrote.
 */
function seededFigure(page: Page) {
  return page.locator(`#sec-${seeded!.sectionId}`).locator(FIGURE).first()
}

const BRANCHING = [
  '```mermaid',
  'graph TD',
  'A[외부 공격자] --> B[무결성 위협]',
  'A --> C[가용성 공격]',
  'A --> D[접근 제어]',
  'B --> B1[가로채기]',
  'C --> C1[의존성]',
  'D --> D1[인증 누락]',
  'style A fill:#f9f,stroke:#333',
  'style B1 fill:#ff0',
  'classDef bad fill:#f00',
  '```',
].join('\n')

/**
 * The report this file seeds, remembered between the seeding and the reading.
 *
 * Five reports in the shared account carry the same title, so opening one by
 * its gallery card's `aria-label` opens whichever of them sorts first — the
 * failure looks like a diagram that would not render and is a document that
 * never had one. Held as an id, and reopened by its own session where it has
 * one, which names exactly one document.
 */
let seeded: { id: string; sectionId: string } | null = null

async function seed(page: Page, body: string) {
  seeded = await openAndSeedReport(page, body, { clearDiagrams: true })
}

test('갈라지는 도해는 세로보다 가로가 길다', async ({ page }) => {
  await seed(page, BRANCHING)
  // The figure, whichever form it is in. It starts as an SVG mermaid just
  // drew and becomes an `<img>` the moment the picture is stored and the
  // store refreshes — which is the component working, not failing. Written
  // against the SVG alone this test resolved a handle to a node that was
  // then replaced under it, and timed out evaluating on the detached one.
  const figure = seededFigure(page)
  await expect(figure).toBeVisible({ timeout: 20_000 })
  // Polled rather than read once. Mermaid lays the diagram out a frame or two
  // after inserting it, and a figure measured before that is a box with no
  // size — which passes a "wider than tall" test for the wrong reason as
  // readily as it fails one.
  const measure = () =>
    seededFigure(page)
      .evaluate((el) => {
        const rect = el.getBoundingClientRect()
        return {
          width: rect.width,
          height: rect.height,
          column: (el.parentElement as HTMLElement).getBoundingClientRect().width,
        }
      })
      // The swap can land between resolving the handle and measuring it.
      // Returning null lets the poll below resolve a fresh one instead of
      // failing the test on a node that was replaced by its own success.
      .catch(() => null)
  let box: Awaited<ReturnType<typeof measure>> = null
  await expect
    .poll(
      async () => {
        box = await measure()
        return box?.width ?? 0
      },
      { timeout: 20_000 },
    )
    .toBeGreaterThan(0)
  box = box!
  expect(box.width, `${box.width}×${box.height} — 세로가 더 길다`).toBeGreaterThan(box.height)
  // And it fits the column rather than needing a scrollbar — the other way a
  // figure goes wrong.
  expect(box.width).toBeLessThanOrEqual(box.column + 1)
})

test('모델이 칠한 색은 그리기 전에 지워진다', async ({ page }) => {
  await seed(page, BRANCHING)
  const svg = page.locator(`#sec-${seeded!.sectionId}`).locator('svg[id^="d"]').first()
  await expect(svg).toBeVisible({ timeout: 20_000 })

  // The three colours the source asked for. Any of them on the page means the
  // figure is drawn in a palette nothing else in the document uses.
  const fills = await svg.evaluate((el) =>
    Array.from(el.querySelectorAll<SVGElement>('*')).map((n) =>
      getComputedStyle(n).fill.replace(/\s/g, ''),
    ),
  )
  for (const asked of ['rgb(255,153,255)', 'rgb(255,255,0)', 'rgb(255,0,0)']) {
    expect(fills, `${asked} 이 남아 있다`).not.toContain(asked)
  }
})

test('흐름도도 그림으로 저장되어 페이지뷰에 나온다', async ({ page }) => {
  // The bug this exists for: every flowchart in every report was drawn and
  // then thrown away.
  //
  // Mermaid's HTML labels put each node's words in a `foreignObject`, and a
  // canvas handed an SVG with foreign content in it is tainted — the drawing
  // succeeds and `toDataURL` throws. `rasterise` caught that and returned
  // null, so nothing was ever posted back, and both ends of the failure looked
  // fine from where they stood: the web view had the diagram on screen, and
  // the page view, which has only the stored picture, showed a placeholder
  // saying it would appear once somebody opened the web view. Somebody had.
  //
  // The suite did not catch it because the test that covers the storing path
  // draws a `pie`, and pie labels are plain `<text>`. So this asserts the same
  // chain for a `graph`, which is the kind of diagram reports actually carry.
  await seed(page, BRANCHING)
  await expect(seededFigure(page)).toBeVisible({ timeout: 20_000 })

  const stored = async () =>
    page.evaluate(async (admin) => {
      const s = await (
        await fetch('/api/auth/login', {
          method: 'POST',
          credentials: 'include',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ email: admin.email, password: admin.password }),
        })
      ).json()
      const headers = { Authorization: `Bearer ${s.accessToken ?? s.access_token}` }
      const full = await (
        await fetch(`/api/artifacts/${admin.id}`, { headers, credentials: 'include' })
      ).json()
      const sections = (full.data ?? full).sections as { diagrams?: Record<string, string> }[]
      return Object.keys(sections[0]?.diagrams ?? {}).length
    }, { ...E2E_ADMIN, id: seeded!.id })

  await expect
    .poll(stored, { timeout: 25_000, message: '흐름도 그림이 서버에 저장되지 않았다' })
    .toBeGreaterThan(0)

  // And the page view then shows that picture rather than the placeholder.
  await page.getByRole('button', { name: '페이지뷰' }).click()
  await expect(page.locator('.page').first()).toBeVisible({ timeout: 30_000 })
  // Named by its source: other runs leave their own diagrams in this document.
  await expect(
    page.locator('.page figure.diagram[data-source*="graph TD"] img[src^="data:image/png"]').first(),
  ).toBeVisible({ timeout: 25_000 })
})
