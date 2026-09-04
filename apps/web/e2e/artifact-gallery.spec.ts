import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** The gallery lists one page of partial rows; counts and search cover the whole workspace. */

const PAGE = 60

const AS_USER = `async (path, init) => {
  const login = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'e2e-personas@example.com', password: 'personas-playwright-pass' }),
  })
  const { accessToken } = await login.json()
  const r = await fetch(path, { ...init, headers: { ...(init?.headers || {}), Authorization: 'Bearer ' + accessToken } })
  return r.ok ? await r.json() : null
}`

test('결과물 목록은 한 페이지씩 오고, 개수와 검색은 전체를 본다', async ({ page }) => {
  test.setTimeout(240_000)
  await signIn(page)

  // Fill the workspace past one page, with a known search hit pushed beyond the first page.
  await page.evaluate(
    async ([fn, page_]) => {
      await eval(fn as string)('/api/artifacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: 'code',
          title: `보안 검색 표적 ${Date.now()}`,
          data: { kind: 'code', content: 'print("audit")', language: 'python' },
        }),
      })
      for (let i = 0; i <= (page_ as number); i++) {
        await eval(fn as string)('/api/artifacts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: 'code',
            title: `목록 채우기 ${i}`,
            data: { kind: 'code', content: `print(${i})`, language: 'python' },
          }),
        })
      }
    },
    [AS_USER, PAGE] as const,
  )

  // Measured on a reload: signing in already requests this page.
  await page.goto('/artifacts')
  const firstPage = page.waitForResponse(
    (r) => r.url().includes('/api/artifacts?') && r.request().method() === 'GET' && r.ok(),
  )
  await page.reload()
  const response = await firstPage
  const rows = (await response.json()) as { id: string; kind: string; partial?: boolean }[]
  const bytes = (await response.body()).length

  // One page, at the server's page size.
  expect(rows.length).toBeLessThanOrEqual(PAGE)
  expect(bytes).toBeLessThan(400_000)
  // Written documents arrive as partial cards.
  expect(rows.some((row) => row.partial)).toBe(true)

  // Tabs count the workspace; the number arrives on its own request.
  const all = page.getByRole('tab', { name: /전체/ })
  await expect(all).toHaveText(/전체\s*\d+/, { timeout: 20_000 })
  const total = Number((await all.textContent())?.match(/\d+/)?.[0] ?? '0')
  expect(total).toBeGreaterThan(rows.length)

  const cards = page.getByRole('button', { name: /열기$/ })
  await expect(cards).toHaveCount(rows.length, { timeout: 20_000 })

  // Anchored: the sidebar has its own "이전 대화 N개 더 보기".
  const more = page.getByRole('button', { name: /^\d+개 더 보기$/ })
  await expect(more).toBeVisible({ timeout: 20_000 })
  await more.click()
  await expect(cards).not.toHaveCount(rows.length, { timeout: 20_000 })
  const grown = await cards.count()
  expect(grown).toBeGreaterThan(rows.length)

  const searched = page.waitForResponse(
    (r) => r.url().includes('/api/artifacts?') && r.url().includes('q=') && r.ok(),
  )
  await page.getByLabel('아티팩트 검색').fill('보안')
  const hits = (await (await searched).json()) as { title: string }[]
  expect(hits.length).toBeGreaterThan(0)
  // Every row matches.
  expect(hits.every((row) => row.title.includes('보안'))).toBe(true)
  await expect(cards).toHaveCount(hits.length, { timeout: 20_000 })
  await page.screenshot({ path: 'test-results/shots/15-artifact-search.png' })
})

test('카드에 없던 본문은 문서를 열 때 채워진다', async ({ page }) => {
  test.setTimeout(180_000)
  await signIn(page)

  await page.evaluate(
    async (fn) =>
      await eval(fn as string)('/api/artifacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: 'html',
          title: `본문 지연 로드 ${Date.now()}`,
          data: {
            content: '<!doctype html><html><body><h1>지연 로드 확인</h1></body></html>',
          },
        }),
      }),
    AS_USER,
  )

  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /HTML/ }).click()
  const card = page.getByRole('button', { name: /열기$/ }).first()
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  // The listing row carried no markup; the source tab proves the document was fetched.
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: '소스' }).click()
  // DOCTYPE case varies between model output and 서식 seeds.
  await expect(dialog.locator('pre')).toContainText(/<!doctype html>/i, { timeout: 20_000 })
})
