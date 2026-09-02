import { expect, test } from '@playwright/test'
import { artifactReady, signIn } from './helpers'

/**
 * What the linter found, shown on the artifact that carries it.
 *
 * The rules themselves are pinned in `tests/test_lint.py`, where a bad
 * sentence can be written on purpose. A model cannot be asked to produce one
 * reliably, so what this covers is the other half: that findings stored on an
 * artifact reach the panel, and that a document generated for real comes back
 * carrying the field at all.
 */

const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, {
    ...(init ?? {}),
    headers: { Authorization: 'Bearer ' + accessToken, 'Content-Type': 'application/json' },
  })
  return r.ok ? await r.json() : null
}`

test('검사에서 걸린 곳은 결과물 패널에서 셀 수 있고 읽을 수 있다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const title = `검사 표시 ${Date.now()}`
  const artifact = await page.evaluate(
    async ([fn, name]) =>
      await eval(fn)('/api/artifacts', {
        method: 'POST',
        body: JSON.stringify({
          kind: 'report',
          title: name,
          data: {
            sections: [
              { id: 's1', heading: '배경', level: 1, status: 'done', content: '본문입니다.' },
            ],
            sources: [],
            citationStyle: 'APA',
            wordCount: 3,
            lint: [
              {
                severity: 'P0',
                rule: 'placeholder',
                message: '채우지 않은 자리가 남았습니다 — “여기에 내용을 입력”.',
                where: '배경',
              },
              {
                severity: 'P1',
                rule: 'filler',
                message: '채움말이 있습니다 — “혁신적”.',
                where: '요약',
              },
            ],
          },
        }),
      }),
    [AS_USER, title],
  )
  expect(artifact, '아티팩트를 만들지 못했습니다').not.toBeNull()

  await page.goto('/artifacts')
  // Opened by its own name, and waited for first: the grid arrives from its
  // own request, and the sidebar carries session titles that read the same.
  const card = page.getByRole('button', { name: `${title} 열기` })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  // The count is the P0 one: "one thing is wrong" and "two could read better"
  // are different sentences, and only the first should look urgent.
  // 검사 결과는 리본의 검토 칸에 있다. A panel opens on 홈, so a test that
  // reaches for it without saying which tab is looking at the wrong row.
  await page.getByRole('tab', { name: '검토', exact: true }).click()
  const badge = page.getByRole('button', { name: '검사 결과' })
  await expect(badge).toBeVisible({ timeout: 20_000 })
  await expect(badge).toContainText('고칠 곳 1')

  await badge.click()
  await expect(page.getByText(/채우지 않은 자리가 남았습니다/)).toBeVisible()
  await page.screenshot({ path: 'test-results/shots/10-lint-findings.png' })
  await expect(page.getByText(/채움말이 있습니다/)).toBeVisible()
  // Each finding says where it is, or the reader has to hunt for it. Scoped
  // to the list: the same heading is also in the document, its table of
  // contents, and the section it names.
  await expect(page.getByRole('list').getByText('배경', { exact: true })).toBeVisible()
})

test('검토를 받으면 점수와 지적이 같은 자리에 함께 선다', async ({ page }) => {
  test.setTimeout(300_000)
  await signIn(page)

  const title = `검토 대상 ${Date.now()}`
  await page.evaluate(
    async ([fn, name]) =>
      await eval(fn)('/api/artifacts', {
        method: 'POST',
        body: JSON.stringify({
          kind: 'report',
          title: name,
          data: {
            sections: [
              {
                id: 's1',
                heading: '배경',
                level: 1,
                status: 'done',
                content:
                  '학과 서버는 보증이 끝났고 지난해 장애가 세 번 있었다. 교체와 유지 가운데 하나를 2분기 안에 정해야 한다.',
              },
              {
                id: 's2',
                heading: '대안',
                level: 1,
                status: 'done',
                content: '유지하면 비용이 없다. 교체하면 예산 상신이 필요하다.',
              },
            ],
            sources: [],
            citationStyle: 'APA',
            wordCount: 40,
            lint: [],
          },
        }),
      }),
    [AS_USER, title],
  )

  await page.goto('/artifacts')
  const card = page.getByRole('button', { name: `${title} 열기` })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  // Offered even with nothing found automatically: the review is the thing
  // that costs a call, so it is asked for rather than run.
  // 검사 결과는 리본의 검토 칸에 있다. A panel opens on 홈, so a test that
  // reaches for it without saying which tab is looking at the wrong row.
  await page.getByRole('tab', { name: '검토', exact: true }).click()
  const badge = page.getByRole('button', { name: '검사 결과' })
  await expect(badge).toBeVisible({ timeout: 20_000 })
  await badge.click()
  await expect(page.getByText(/모델을 한 번 호출합니다/)).toBeVisible()
  await page.getByRole('button', { name: '검토 받기' }).click()

  // The score lands beside the findings, in the one list of things to look at.
  await expect(page.getByText(/검토 \d+(\.\d)?\/10/)).toBeVisible({ timeout: 240_000 })
  await page.screenshot({ path: 'test-results/shots/12-critique.png' })

  const stored = await page.evaluate(
    async ([fn, name]) => {
      const rows = await eval(fn)('/api/artifacts')
      const list = Array.isArray(rows) ? rows : rows.items
      return list.find((a: { title: string }) => a.title === name) ?? null
    },
    [AS_USER, title],
  )
  expect(stored.data.critique.score).toBeGreaterThanOrEqual(0)
  expect(stored.data.critique.score).toBeLessThanOrEqual(10)
  // A review annotates rather than edits: the document and its version stand.
  expect(stored.version).toBe(1)
  expect(stored.data.sections).toHaveLength(2)
})

test('걸린 것이 없으면 개수 대신 검토를 권한다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const title = `검사 없음 ${Date.now()}`
  await page.evaluate(
    async ([fn, name]) =>
      await eval(fn)('/api/artifacts', {
        method: 'POST',
        body: JSON.stringify({
          kind: 'report',
          title: name,
          data: {
            sections: [
              { id: 's1', heading: '배경', level: 1, status: 'done', content: '본문입니다.' },
            ],
            sources: [],
            citationStyle: 'APA',
            wordCount: 3,
            lint: [],
          },
        }),
      }),
    [AS_USER, title],
  )

  await page.goto('/artifacts')
  const card = page.getByRole('button', { name: `${title} 열기` })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()
  await artifactReady(page, 20_000)
  // Nothing was found, so the badge says what it *can* offer — a review —
  // rather than a count of problems that do not exist.
  // 검사 결과는 리본의 검토 칸에 있다. A panel opens on 홈, so a test that
  // reaches for it without saying which tab is looking at the wrong row.
  await page.getByRole('tab', { name: '검토', exact: true }).click()
  const badge = page.getByRole('button', { name: '검사 결과' })
  await expect(badge).toContainText('검토')
  await expect(badge).not.toContainText('고칠 곳')
})
