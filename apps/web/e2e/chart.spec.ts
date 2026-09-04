import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** The chart tool: numbers from the model, colours assigned server-side, table derived from the plotted points. */

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
  // Numbers supplied, so nothing has to be invented.
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
  // Colours are assigned server-side.
  for (const one of series) expect(one.color).toMatch(/^#[0-9a-f]{6}$/i)
  // A non-numeric point is dropped, not plotted as zero.
  for (const point of series[0].points) expect(typeof point.y).toBe('number')

  // The table is derived: one row per x, one column per series plus the axis.
  const table = chart.data.table as { columns: string[]; rows: (string | number)[][] }
  expect(table.columns).toHaveLength(series.length + 1)
  expect(table.rows).toHaveLength(series[0].points.length)

  // Scoped to the dialog: 데이터 also names things in the sidebar.
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^차트/ }).click()
  await page.locator('button.aspect-video').first().click()
  const panel = page.getByRole('dialog')
  await expect(panel.locator('svg[role="img"]').first()).toBeVisible({ timeout: 20_000 })
  await panel.getByRole('button', { name: '데이터' }).click()
  await expect(panel.locator('table')).toBeVisible()

  const csv = page.waitForEvent('download', { timeout: 30_000 })
  await panel.getByRole('button', { name: '내보내기', exact: true }).click()
  await page.getByRole('menuitem', { name: '데이터' }).click()
  const csvFile = await csv
  expect(csvFile.suggestedFilename()).toMatch(/\.csv$/)
  // A BOM, so Excel reads the Korean.
  expect((await readFile(await csvFile.path())).subarray(0, 3)).toEqual(
    Buffer.from([0xef, 0xbb, 0xbf]),
  )

  const svg = page.waitForEvent('download', { timeout: 30_000 })
  await panel.getByRole('button', { name: '내보내기', exact: true }).click()
  await page.getByRole('menuitem', { name: '벡터' }).click()
  const source = (await readFile(await (await svg).path())).toString('utf8')
  // CSS custom properties must be resolved at export.
  expect(source).not.toContain('var(--')
  expect(source).toContain('<svg')
})
