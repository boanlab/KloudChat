import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * Charts.
 *
 * A tool the model calls. Only the numbers come from the model; the colours are
 * decided here. The table is derived from the same points the graph plots — the
 * two cannot diverge, which is what keeps "the rows this chart was computed
 * from" a true statement.
 */

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

test('수치를 주고 차트를 부탁하면 차트 아티팩트가 생긴다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  const before = await page.evaluate(async (fn) => {
    const rows = await eval(fn)('/api/artifacts')
    const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
    return list.filter((a: { kind: string }) => a.kind === 'chart').map((a: { id: string }) => a.id)
  }, AS_USER)

  await page.goto('/new/chat')
  // Numbers supplied in the prompt, so nothing has to be invented — a chart of
  // made-up figures is the failure mode this surface has to avoid.
  await page.getByLabel('프롬프트 입력').fill(
    '아래 수치를 막대 차트로 그려줘. create_chart 도구를 써.\n' +
      '2021년 12건, 2022년 19건, 2023년 31건, 2024년 44건. 축 이름은 연도와 건수.',
  )
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })

  const fresh = async () =>
    await page.evaluate(
      async ([fn, seen]) => {
        const rows = await eval(fn as string)('/api/artifacts')
        const list = Array.isArray(rows) ? rows : (rows?.items ?? [])
        return (
          list.find(
            (a: { kind: string; id: string }) =>
              a.kind === 'chart' && !(seen as string[]).includes(a.id),
          ) ?? null
        )
      },
      [AS_USER, before] as const,
    )

  await expect
    .poll(fresh, { timeout: 150_000, message: '차트 아티팩트가 생기지 않았습니다' })
    .not.toBeNull()
  const chart = await fresh()

  const series = chart.data.series as { name: string; color: string; points: { x: string; y: number }[] }[]
  expect(series.length).toBeGreaterThan(0)
  expect(series[0].points.length).toBeGreaterThanOrEqual(3)
  // Assigned server-side. A model asked for colours answers "blue" as often as
  // a hex value, and two series in the same red is worse than no choice at all.
  for (const one of series) expect(one.color).toMatch(/^#[0-9a-f]{6}$/i)
  // Every y is a number: a non-numeric point is dropped rather than plotted as
  // zero, which would draw "no data" as if it meant "none".
  for (const point of series[0].points) expect(typeof point.y).toBe('number')

  // The table is derived, so it has one row per x and one column per series
  // plus the axis. It cannot disagree with the plot because it is not asked for.
  const table = chart.data.table as { columns: string[]; rows: (string | number)[][] }
  expect(table.columns).toHaveLength(series.length + 1)
  expect(table.rows).toHaveLength(series[0].points.length)

  // The panel renders it, and the data tab shows the same rows. Scoped to the
  // dialog: 데이터 ("data") also names things in the sidebar behind it.
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^차트/ }).click()
  await page.locator('button.aspect-video').first().click()
  const panel = page.getByRole('dialog')
  await expect(panel.locator('svg[role="img"]').first()).toBeVisible({ timeout: 20_000 })
  await panel.getByRole('button', { name: '데이터' }).click()
  await expect(panel.locator('table')).toBeVisible()

  // The export menu is wired — all four items were decoration before.
  const csv = page.waitForEvent('download', { timeout: 30_000 })
  await panel.getByRole('button', { name: '내보내기' }).click()
  await page.getByRole('menuitem', { name: '데이터' }).click()
  const csvFile = await csv
  expect(csvFile.suggestedFilename()).toMatch(/\.csv$/)
  // A BOM, so Excel reads the Korean instead of mojibake.
  expect((await readFile(await csvFile.path())).subarray(0, 3)).toEqual(
    Buffer.from([0xef, 0xbb, 0xbf]),
  )

  const svg = page.waitForEvent('download', { timeout: 30_000 })
  await panel.getByRole('button', { name: '내보내기' }).click()
  await page.getByRole('menuitem', { name: '벡터' }).click()
  const source = (await readFile(await (await svg).path())).toString('utf8')
  // The plot styles itself from CSS custom properties, which mean nothing once
  // the file leaves the page — an export that keeps them opens as invisible
  // lines on nothing. They have to be resolved at save time.
  expect(source).not.toContain('var(--')
  expect(source).toContain('<svg')
})
