/** Diagram shape and colour: a branching graph is drawn wider than tall, model `style` lines are
 *  stripped, and flowcharts are stored as pictures for the page view. */
import { expect, test, type Page } from '@playwright/test'
import { E2E_ADMIN, openAndSeedReport } from './helpers'

/** The figure in either form: mermaid's `<svg>`, or the `<img>` it becomes once stored. */
const FIGURE = 'svg[id^="d"], figure img, img[src^="data:image/png"]'

/** The figure inside the seeded section; other sections carry other runs' diagrams. */
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

/** The report this file seeds; titles are not unique, so it is held by id. */
let seeded: { id: string; sectionId: string } | null = null

async function seed(page: Page, body: string) {
  seeded = await openAndSeedReport(page, body, { clearDiagrams: true })
}

test('갈라지는 도해는 세로보다 가로가 길다', async ({ page }) => {
  await seed(page, BRANCHING)
  const figure = seededFigure(page)
  await expect(figure).toBeVisible({ timeout: 20_000 })
  // Polled: mermaid lays the diagram out a frame or two after inserting it.
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
      // The svg→img swap can land mid-measure; null lets the poll resolve a fresh handle.
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
  // And fits the column.
  expect(box.width).toBeLessThanOrEqual(box.column + 1)
})

test('모델이 칠한 색은 그리기 전에 지워진다', async ({ page }) => {
  await seed(page, BRANCHING)
  const svg = page.locator(`#sec-${seeded!.sectionId}`).locator('svg[id^="d"]').first()
  await expect(svg).toBeVisible({ timeout: 20_000 })

  // The three colours the source asked for.
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
  // A `graph` (unlike `pie`) puts labels in `foreignObject`, which taints a canvas; the
  // rasterise-and-store chain must still work for it.
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

  // The page view shows that picture.
  await page.getByRole('button', { name: '페이지뷰' }).click()
  await expect(page.locator('.page').first()).toBeVisible({ timeout: 30_000 })
  // Named by its source.
  await expect(
    page.locator('.page figure.diagram[data-source*="graph TD"] img[src^="data:image/png"]').first(),
  ).toBeVisible({ timeout: 25_000 })
})
