import { readFile } from 'node:fs/promises'
import { inflateRawSync } from 'node:zlib'
import { expect, test } from '@playwright/test'
import { approvePlan, artifactReady, ribbonTab, signIn } from './helpers'

/**
 * Retried once, and only here among the design suites.
 *
 * This is the longest test in the file by an order of magnitude: one run makes
 * a deck outline, a slide per page, a report outline, a section per heading and
 * two exports — a dozen model calls whose success has to multiply out. Each of
 * them lands almost always; all of them landing is a coin that comes up tails
 * often enough to report the model's afternoon as a product defect.
 *
 * Once rather than twice: at nine minutes a run, a second retry buys a little
 * and costs half an hour.
 */
test.describe.configure({ retries: 1 })


/**
 * A design system, from the screen it is written on to the file it comes out of.
 *
 * One deck and one report per run — a model call for each outline and one per
 * slide or section — so both are generated once and the accent, the face and
 * the exports are all asserted against them.
 *
 * The image surface is deliberately not exercised here: the composed prompt is
 * not stored (the stored one stays what the person typed), so there is nothing
 * to assert without spending ~4,400 credits on a picture nobody looks at. That
 * path is covered in `tests/test_design_system.py`.
 */

/** Not one of `deck._THEMES`: a palette colour would also pass if the model's
 *  own answer were still being read. */
const ACCENT = '#7a1f3d'

/** One member of a zip, inflated. Enough of the format to read a slide part. */
function unzip(buffer: Buffer, name: string): string | null {
  for (let i = 0; i < buffer.length - 46; i++) {
    if (buffer.readUInt32LE(i) !== 0x02014b50) continue // central directory header
    const nameLength = buffer.readUInt16LE(i + 28)
    if (buffer.toString('utf8', i + 46, i + 46 + nameLength) !== name) continue

    const method = buffer.readUInt16LE(i + 10)
    const compressed = buffer.readUInt32LE(i + 20)
    const local = buffer.readUInt32LE(i + 42)
    // The local header repeats the name and carries its own extra field, so
    // the data offset cannot be taken from the central entry.
    const start = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28)
    const bytes = buffer.subarray(start, start + compressed)
    return (method === 8 ? inflateRawSync(bytes) : bytes).toString('utf8')
  }
  return null
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

test('디자인 시스템을 프로젝트에 붙이면 덱과 보고서, 내보낸 파일까지 따라온다', async ({ page }) => {
  test.setTimeout(600_000)
  await signIn(page)

  // ── 1. Write one ────────────────────────────────────────────────────
  const name = `디자인 검증 ${Date.now()}`
  await page.goto('/designs')
  const designs = page.getByRole('region', { name: '디자인 시스템' })
  await designs.getByRole('button', { name: '디자인 추가' }).click()

  await designs.getByLabel('이름', { exact: true }).fill(name)
  await designs.getByLabel('한 줄 설명').fill('검증용 자주색 명조')
  await designs.getByLabel('강조색 색상 코드').fill(ACCENT)
  await designs.getByLabel('서체').selectOption('serif')
  await designs.getByLabel(/문체 규율/).fill('제목은 명사구로 쓴다.')
  await designs.getByLabel('이미지 스타일').fill('muted documentary photography')
  await designs.getByRole('checkbox', { name: /글의 결 맞추기/ }).check()
  await designs.getByRole('button', { name: '저장', exact: true }).click()

  const row = designs.locator('li', { hasText: name })
  await expect(row).toBeVisible({ timeout: 20_000 })

  // ── 2. Put it on a project ──────────────────────────────────────────
  const projectName = `디자인 프로젝트 ${Date.now()}`
  await page.goto('/projects')
  await page.getByRole('button', { name: '새 프로젝트' }).click()
  await page.getByLabel('이름', { exact: true }).fill(projectName)
  // Creating navigates straight into the project — the id comes from the
  // server, so this is also the wait for the write to have landed.
  await page.getByRole('button', { name: '만들기', exact: true }).click()
  await expect(page).toHaveURL(/\/projects\/[0-9a-f]{32}/, { timeout: 20_000 })
  await expect(page.getByRole('heading', { name: projectName })).toBeVisible({ timeout: 20_000 })
  const projectId = page.url().split('/projects/')[1]

  const picker = page.getByLabel('디자인', { exact: true })
  await expect(picker).toBeVisible({ timeout: 20_000 })
  // The picker writes optimistically, so the screen says “saved” before the
  // server has been told. Waiting on the PATCH itself is what makes the reload
  // below a test of the column rather than a race against a request the reload
  // would otherwise cancel.
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/projects/${projectId}`) &&
      r.request().method() === 'PATCH' &&
      r.status() === 200,
    { timeout: 20_000 },
  )
  await picker.selectOption({ label: name })
  // The description under the picker confirms the row behind the option was
  // resolved, not merely that an option was selected.
  await expect(page.getByText('검증용 자주색 명조')).toBeVisible({ timeout: 20_000 })
  expect(((await (await saved).json()) as { designSystemId: string }).designSystemId).toMatch(
    /^[0-9a-f]{32}$/,
  )

  // It survives a reload — this is a column, not component state.
  await page.reload()
  await expect(page.getByLabel('디자인', { exact: true })).toHaveValue(/[0-9a-f]{32}/, {
    timeout: 20_000,
  })

  // ── 3. Make a deck inside that project ──────────────────────────────
  await page.getByRole('button', { name: '이 프로젝트에서 새로 만들기' }).click()
  await page.getByRole('menuitem', { name: '슬라이드' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const sessionId = page.url().split('/s/')[1]

  await page.getByLabel('프롬프트 입력').fill('연구실 세미나에서 쓸 발표 슬라이드를 만들어줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  // Planned first and written only once approved — there is no deck, and so no
  // export, before this.
  await approvePlan(page, 480_000)

  await artifactReady(page)

  // ── 4. The artifact wears it ────────────────────────────────────────
  const stored = await page.evaluate(
    async ([fn, id]) => {
      const rows = await eval(fn)('/api/artifacts')
      const list = Array.isArray(rows) ? rows : rows.items
      const row = list.find(
        (a: { kind: string; sessionId: string }) => a.kind === 'deck' && a.sessionId === id,
      )
      // A listing row is a card: four slide titles and no bodies. Read the deck.
      return row ? await eval(fn)('/api/artifacts/' + row.id) : null
    },
    [AS_USER, sessionId],
  )
  expect(stored, '이 세션의 덱 아티팩트가 없습니다').not.toBeNull()

  const slides = stored.data.slides as { accent?: string }[]
  expect(slides.length).toBeGreaterThanOrEqual(5)
  // Every slide, not just the cover: the accent is per slide in the artifact.
  expect(new Set(slides.map((s) => s.accent))).toEqual(new Set([ACCENT]))
  // Snapshotted onto the artifact, so the export does not have to look the
  // project up — and so a deck does not repaint itself later.
  expect(stored.data.design).toMatchObject({ accent: ACCENT, font: 'serif' })

  // ── 5. …and so does the file ────────────────────────────────────────
  await ribbonTab(page, '파일')
  const exportButton = page.getByRole('button', { name: '내보내기', exact: true })
  const download = page.waitForEvent('download', { timeout: 60_000 })
  await exportButton.click()
  await page.getByRole('menuitem', { name: 'PowerPoint' }).click()
  const file = await download
  const slideXml = unzip(await readFile(await file.path()), 'ppt/slides/slide1.xml')

  expect(slideXml, 'pptx 에 첫 슬라이드가 없습니다').not.toBeNull()
  // OOXML writes colours as bare uppercase hex.
  expect(slideXml).toContain(ACCENT.slice(1).toUpperCase())
  // The design's face reached PowerPoint's East Asian slot, which is the one
  // Hangul is actually laid out with.
  expect(slideXml).toContain('바탕')
  expect(slideXml).not.toContain('맑은 고딕')

  // ── 6. The other document surface, from the same project ────────────
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('button', { name: '이 프로젝트에서 새로 만들기' }).click()
  await page.getByRole('menuitem', { name: '보고서' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 60_000 })
  const reportSession = page.url().split('/s/')[1]

  await page.getByLabel('프롬프트 입력').fill('연구실 장비 관리 현황을 정리한 짧은 보고서를 써줘')
  await page.getByLabel('프롬프트 입력').press('Enter')
  // The turn ends on a proposal and writes nothing; the card is what writes.
  await approvePlan(page, 480_000)

  const report = await page.evaluate(
    async ([fn, id]) => {
      const rows = await eval(fn)('/api/artifacts')
      const list = Array.isArray(rows) ? rows : rows.items
      const row = list.find(
        (a: { kind: string; sessionId: string }) => a.kind === 'report' && a.sessionId === id,
      )
      // A card keeps the top of four sections and no citations; read the report.
      return row ? await eval(fn)('/api/artifacts/' + row.id) : null
    },
    [AS_USER, reportSession],
  )
  expect(report, '이 세션의 보고서 아티팩트가 없습니다').not.toBeNull()
  expect(report.data.design).toMatchObject({ accent: ACCENT, font: 'serif' })

  // `.hwpx` is the format with the least room for argument: Hancom parses the
  // XML, so a colour written into the wrong place is a file that will not open
  // rather than a document that looks off.
  // 내보내기는 리본의 파일 칸에 있다.
  await ribbonTab(page, '파일')
  const hwpxDownload = page.waitForEvent('download', { timeout: 60_000 })
  await page.getByRole('button', { name: '내보내기', exact: true }).click()
  await page.getByRole('menuitem', { name: '한글 문서' }).click()
  const hwpx = await hwpxDownload
  expect(hwpx.suggestedFilename()).toMatch(/\.hwpx$/)

  const header = unzip(await readFile(await hwpx.path()), 'Contents/header.xml')
  expect(header, 'hwpx 에 header.xml 이 없습니다').not.toBeNull()
  // Title and section headings take the accent; body text stays black.
  expect(header).toContain(`id="2" height="1600" textColor="${ACCENT}"`)
  expect(header).toContain(`id="3" height="1300" textColor="${ACCENT}"`)
  expect(header).toContain('id="0" height="1000" textColor="#000000"')
})
