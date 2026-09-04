import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

/** Version history works on html, deck and report panels; a restore is itself an edit. */

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

/** Creates an artifact and edits it once, so there is one revision to go back to. */
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
  // v1 is now a stored revision; the document is v2.
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

  // The HTML panel has no ribbon, so the button is directly on it.
  const history = dialog.getByRole('button', { name: '버전 기록' })
  await expect(history).toContainText('v2')
  await history.click()

  // The edit's summary tells revisions apart.
  await expect(page.getByText('블록 다시 쓰기')).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: 'v1 로 되돌리기' }).click()

  // The document, not just the version number.
  await expect(dialog.locator('pre')).toContainText('처음 쓴 문단', { timeout: 20_000 })
  await expect(history).toContainText('v3')

  // A restore is an edit: the revision it replaced stays in the list.
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

  await dialog.getByRole('tab', { name: '검토', exact: true }).click()
  const history = dialog.getByRole('button', { name: '버전 기록' })
  await expect(history).toContainText('v2')
  await history.click()
  await page.getByRole('button', { name: 'v1 로 되돌리기' }).click()

  // The slide itself comes back.
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

  await dialog.getByRole('tab', { name: '검토', exact: true }).click()
  const history = dialog.getByRole('button', { name: '버전 기록' })
  await history.click()
  await page.getByRole('button', { name: 'v1 로 되돌리기' }).click()
  await expect(dialog.getByText('처음 쓴 본문입니다.')).toBeVisible({ timeout: 20_000 })
  await expect(history).toContainText('v3')
})
