import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/**
 * 버전 기록은 종류를 가리지 않는다.
 *
 * The endpoints never did — every write snapshots the revision it replaces, and
 * `versions`/`restore` take an id and nothing else. Only the report panel ever
 * listed them, so a deck or an HTML document piled up revisions with no way
 * back to one. What this covers is the same control answering on all three
 * screens, and a restore behaving the way the server describes it: an edit,
 * which leaves the revision it replaced in the list behind it.
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

/** Creates an artifact and edits it once, so there is exactly one revision
 *  behind the document to go back to. */
async function seedEdited(
  page: import('@playwright/test').Page,
  kind: string,
  title: string,
  first: Record<string, unknown>,
  second: Record<string, unknown>,
  summary: string,
) {
  const made = await page.evaluate(
    async ([fn, k, name, data]) =>
      await eval(fn as string)('/api/artifacts', {
        method: 'POST',
        body: JSON.stringify({ kind: k, title: name, data }),
      }),
    [AS_USER, kind, title, first] as const,
  )
  expect(made, '아티팩트를 만들지 못했습니다').not.toBeNull()
  const edited = await page.evaluate(
    async ([fn, id, data, note]) =>
      await eval(fn as string)(`/api/artifacts/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ data, summary: note }),
      }),
    [AS_USER, (made as { id: string }).id, second, summary] as const,
  )
  // The PATCH is what makes the history: v1 is now a stored revision and the
  // document on screen is v2.
  expect((edited as { version: number }).version).toBe(2)
  return (made as { id: string }).id
}

test('HTML 문서도 되돌릴 수 있고, 되돌리기 자체가 판으로 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const title = `버전 기록 문서 ${Date.now().toString(36)}`
  const before = '<!doctype html><html><body><h1>처음 쓴 문단</h1></body></html>'
  const after = '<!doctype html><html><body><h1>고쳐 쓴 문단</h1></body></html>'
  await seedEdited(
    page,
    'html',
    title,
    { content: before, blocks: [{ title: '머리말', layout: 'text' }] },
    { content: after, blocks: [{ title: '머리말', layout: 'text' }] },
    '블록 다시 쓰기',
  )

  await page.goto('/artifacts')
  const card = page.getByRole('button', { name: `${title} 열기` })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: '소스' }).click()
  await expect(dialog.locator('pre')).toContainText('고쳐 쓴 문단', { timeout: 20_000 })

  // 이 버튼이 이 화면에 있다는 것이 이 수정의 전부다.
  const history = dialog.getByRole('button', { name: '버전 기록' })
  await expect(history).toContainText('v2')
  await history.click()

  // The summary the edit was saved under is what tells one revision from
  // another; without it the dialog is a column of version numbers.
  await expect(page.getByText('블록 다시 쓰기')).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: 'v1 로 되돌리기' }).click()

  // The document, not just the number: a restore that moved the version and
  // left the markup alone would pass a version-only assertion.
  await expect(dialog.locator('pre')).toContainText('처음 쓴 문단', { timeout: 20_000 })
  await expect(history).toContainText('v3')

  // 되돌리기도 편집이다. 되돌리기 직전의 판이 목록에 남아 있어야, 되돌린 것을
  // 다시 되돌릴 수 있다.
  await history.click()
  await expect(page.getByRole('button', { name: 'v2 로 되돌리기' })).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByRole('button', { name: 'v1 로 되돌리기' })).toBeVisible()
})

test('덱도 같은 버튼으로 되돌린다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const title = `버전 기록 덱 ${Date.now().toString(36)}`
  const slide = (heading: string) => ({
    kind: 'deck',
    theme: 'signal',
    slides: [
      {
        id: 's1',
        layout: 'bullets',
        title: heading,
        bullets: ['첫째 줄', '둘째 줄'],
      },
    ],
  })
  await seedEdited(page, 'deck', title, slide('처음 제목'), slide('고친 제목'), '1장 편집')

  await page.goto('/artifacts')
  const card = page.getByRole('button', { name: `${title} 열기` })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('고친 제목').first()).toBeVisible({ timeout: 20_000 })

  const history = dialog.getByRole('button', { name: '버전 기록' })
  await expect(history).toContainText('v2')
  await history.click()
  await page.getByRole('button', { name: 'v1 로 되돌리기' }).click()

  // 무대에 걸린 장이 돌아온다 — 목록의 판 이름이 아니라 슬라이드 자체로 본다.
  await expect(dialog.getByText('처음 제목').first()).toBeVisible({ timeout: 20_000 })
  await expect(history).toContainText('v3')
})

test('보고서의 되돌리기는 옮겨진 뒤에도 그대로다', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page)

  const title = `버전 기록 보고서 ${Date.now().toString(36)}`
  const report = (body: string) => ({
    title,
    sections: [{ id: 's1', heading: '배경', level: 1, status: 'done', content: body }],
    sources: [],
    citationStyle: 'APA',
    wordCount: 3,
  })
  await seedEdited(
    page,
    'report',
    title,
    report('처음 쓴 본문입니다.'),
    report('고쳐 쓴 본문입니다.'),
    '문서 편집',
  )

  await page.goto('/artifacts')
  const card = page.getByRole('button', { name: `${title} 열기` })
  await expect(card).toBeVisible({ timeout: 20_000 })
  await card.click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('고쳐 쓴 본문입니다.')).toBeVisible({ timeout: 20_000 })

  const history = dialog.getByRole('button', { name: '버전 기록' })
  await history.click()
  await page.getByRole('button', { name: 'v1 로 되돌리기' }).click()
  await expect(dialog.getByText('처음 쓴 본문입니다.')).toBeVisible({ timeout: 20_000 })
  await expect(history).toContainText('v3')
})
