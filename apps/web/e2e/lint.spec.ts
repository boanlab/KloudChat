import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

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

test('검사에서 아무것도 걸리지 않으면 배지도 없다', async ({ page }) => {
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
  await expect(page.getByRole('button', { name: '내보내기', exact: true })).toBeVisible({
    timeout: 20_000,
  })
  // A badge that is always there is a badge nobody reads.
  await expect(page.getByRole('button', { name: '검사 결과' })).toHaveCount(0)
})
