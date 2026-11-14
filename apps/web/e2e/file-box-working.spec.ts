import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'

/**
 * 파일함 「작업 문서」: the HWP, Word and PowerPoint files and web reports and web slides the person
 * works on, one row per document, newest edit first; filtered by format, searched by title,
 * sorted, paged; each opens in the editor it has in chat, downloads in its own format, and is
 * renamed or deleted in place.
 */

const id = (n: number) => `d${String(n).padStart(31, '0')}`
const base = Date.parse('2026-11-14T03:00:00.000Z')
const doc = (n: number, kind: string, title: string, extra: Record<string, unknown> = {}) => ({
  id: id(n), kind, title, version: 1, sessionId: null, sessionTitle: null, projectId: null,
  projectName: null, projectEmoji: null, createdAt: new Date(base - 86_400_000).toISOString(),
  updatedAt: new Date(base - n * 60_000).toISOString(), ...extra,
})

type Doc = ReturnType<typeof doc>

function documents(): Doc[] {
  return [
    doc(1, 'hwp', '직원 연수 공문', {
      version: 3, sessionId: chatId, sessionTitle: '지원 사업 공고 정리',
      projectId: 'p'.padEnd(32, '0'), projectName: '연수 준비', projectEmoji: '📚',
    }),
    doc(2, 'docx', '연수 결과 보고'),
    doc(3, 'pptx', '2027 사업 계획', { version: 5, sessionId: chatId, sessionTitle: '지원 사업 공고 정리' }),
    doc(4, 'report', '분기 성과 보고서', { version: 2 }),
    doc(5, 'deck', '사업 설명회 발표', { version: 4, sessionId: chatId, sessionTitle: '지원 사업 공고 정리' }),
    // enough web reports for a second page
    ...Array.from({ length: 52 }, (_, i) => doc(10 + i, 'report', `주간 회의록 ${String(i + 1).padStart(2, '0')}`)),
  ]
}

/** `/artifacts/working` and the document actions, answered as the server does. */
async function fixture(page: Page, info: TestInfo) {
  const shell = await composerFixture(page, info)
  const docs = documents()
  const asked: string[] = []
  const renamed: Record<string, unknown>[] = []
  const deleted: string[] = []
  await page.route('**/api/artifacts/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'GET' && path === '/artifacts/working') {
      asked.push(url.search)
      const q = (url.searchParams.get('q') ?? '').toLowerCase()
      const kind = url.searchParams.get('kind')
      const sort = url.searchParams.get('sort') ?? 'updated'
      const limit = Number(url.searchParams.get('limit') ?? 50)
      const start = Number(url.searchParams.get('cursor') ?? 0)
      const named = docs.filter((row) => !q || row.title.toLowerCase().includes(q))
      const all = named.filter((row) => !kind || row.kind === kind)
      all.sort((a, b) =>
        sort === 'title' ? a.title.localeCompare(b.title)
          : sort === 'oldest' ? a.updatedAt.localeCompare(b.updatedAt)
            : b.updatedAt.localeCompare(a.updatedAt))
      const counts = Object.fromEntries(['hwp', 'docx', 'pptx', 'report', 'deck']
        .map((k) => [k, named.filter((row) => row.kind === k).length]))
      return route.fulfill({ json: {
        items: all.slice(start, start + limit), total: all.length,
        next: start + limit < all.length ? String(start + limit) : null, counts,
      } })
    }
    const one = path.match(/^\/artifacts\/([^/]+)$/)
    if (one?.[1] === 'counts') return route.fallback()
    if (one && method === 'PATCH') {
      const body = request.postDataJSON() as Record<string, unknown>
      renamed.push(body)
      const row = docs.find((d) => d.id === one[1])!
      row.title = String(body.title)
      return route.fulfill({ json: { ...row, data: null } })
    }
    if (one && method === 'DELETE') {
      deleted.push(one[1])
      docs.splice(docs.findIndex((d) => d.id === one[1]), 1)
      return route.fulfill({ status: 204, body: '' })
    }
    // the editor opening it reads the document; not this spec's concern
    if (one && method === 'GET') {
      asked.push(`GET ${path}`)
      return route.fulfill({ status: 404, json: { detail: 'not_found' } })
    }
    const attach = path.match(/^\/artifacts\/([^/]+)\/(hwp|docx|pptx)\/session$/)
    if (attach && method === 'POST') {
      asked.push(`POST ${path}`)
      const row = docs.find((d) => d.id === attach[1])!
      return route.fulfill({ json: { ...row, sessionId: (request.postDataJSON() as { sessionId: string }).sessionId, data: null } })
    }
    const file = path.match(/^\/artifacts\/([^/]+)\/(hwp|docx|pptx)$/)
    if (file && method === 'GET') {
      return route.fulfill({
        body: 'PK', contentType: 'application/octet-stream',
        headers: { 'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent('직원 연수 공문.hwpx')}` },
      })
    }
    if (path.endsWith('/export') && method === 'GET') {
      asked.push(`GET ${path}${url.search}`)
      return route.fulfill({ body: '# 분기 성과 보고서', contentType: 'text/markdown' })
    }
    return route.fallback()
  })
  return { ...shell, asked, renamed, deleted }
}

const rows = (page: Page) => page.getByTestId('working-doc-row')

test('작업 문서 lists each document once, newest edit first, and filters, sorts and pages them', async ({ page }, info) => {
  const { unexpected, asked } = await fixture(page, info)
  await page.goto('/files')
  await page.getByRole('tab', { name: /작업 문서/ }).click()
  await expect(page).toHaveURL(/\/files\?tab=working$/)
  await expect(rows(page)).toHaveCount(50)
  // the badge counts every document, not the page
  await expect(page.getByRole('tab', { name: /작업 문서\s*57/ })).toHaveAttribute('aria-selected', 'true')
  await expect(rows(page).first()).toContainText('직원 연수 공문')
  const first = rows(page).first()
  await expect(first).toContainText('한글 문서')
  await expect(first).toContainText('v3')
  await expect(first.getByRole('link', { name: '📚 연수 준비' })).toBeVisible()
  await expect(first.getByRole('link', { name: '지원 사업 공고 정리' })).toBeVisible()
  await expect(rows(page).filter({ hasText: '연수 결과 보고' })).toContainText('대화 없음')

  await page.getByRole('button', { name: '7개 더 보기' }).click()
  await expect(rows(page)).toHaveCount(57)

  // one format, then a title search over it; the format chips keep their counts
  await page.getByRole('button', { name: /^웹 보고서/ }).click()
  await expect(rows(page)).toHaveCount(50)
  await expect(page.getByRole('button', { name: /^PowerPoint\s*1/ })).toBeVisible()
  await page.getByLabel('파일 검색').fill('성과')
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page)).toContainText('분기 성과 보고서')
  await page.getByRole('button', { name: /^모든 형식/ }).click()
  await page.getByLabel('파일 검색').fill('없는 제목')
  await expect(page.getByText('찾는 문서가 없습니다')).toBeVisible()
  await page.getByLabel('파일 검색').fill('')

  await page.getByLabel('정렬').selectOption('title')
  await expect(rows(page).first()).toContainText('2027 사업 계획')
  expect(asked).toContain('?sort=title&limit=50')
  expect(unexpected).toEqual([])
})

test('작업 문서 opens each kind in its editor, downloads it, renames and deletes it', async ({ page }, info) => {
  const { unexpected, asked, renamed, deleted } = await fixture(page, info)
  await page.goto('/files?tab=working')
  await expect(rows(page)).toHaveCount(50)

  // its own format: the office file as kept, a web report as Markdown
  const saving = page.waitForEvent('download')
  await page.getByRole('button', { name: '직원 연수 공문 내려받기' }).click()
  expect((await saving).suggestedFilename()).toBe('직원 연수 공문.hwpx')
  const markdown = page.waitForEvent('download')
  await page.getByRole('button', { name: '분기 성과 보고서 내려받기' }).click()
  expect((await markdown).suggestedFilename()).toBe('분기 성과 보고서.md')
  expect(asked).toContain(`GET /artifacts/${id(4)}/export?format=md`)

  await page.getByRole('button', { name: '연수 결과 보고 이름 바꾸기' }).click()
  const dialog = page.getByRole('dialog', { name: '이름 바꾸기' })
  await dialog.getByLabel('문서 이름').fill('연수 결과 보고 (최종)')
  await dialog.getByRole('button', { name: '저장' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(rows(page).filter({ hasText: '연수 결과 보고 (최종)' })).toHaveCount(1)
  expect(renamed).toEqual([{ title: '연수 결과 보고 (최종)' }])

  await page.getByRole('button', { name: '사업 설명회 발표 삭제' }).click()
  const confirm = page.getByRole('dialog', { name: '사업 설명회 발표 삭제' })
  await expect(confirm).toContainText('버전 기록도 함께 사라집니다')
  await confirm.getByRole('button', { name: '삭제', exact: true }).click()
  await expect(rows(page).filter({ hasText: '사업 설명회 발표' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: /작업 문서\s*56/ })).toBeVisible()
  expect(deleted).toEqual([id(5)])

  // in its conversation, beside it
  await page.getByRole('button', { name: '2027 사업 계획 편집기에서 열기' }).click()
  await expect(page).toHaveURL(new RegExp(`/s/${chatId}`))
  await expect.poll(() => asked).toContain(`GET /artifacts/${id(3)}`)

  // an office file with no conversation joins a new chat
  await page.goto('/files?tab=working')
  await page.getByRole('button', { name: '연수 결과 보고 (최종)', exact: true }).click()
  await expect(page).toHaveURL(/\/s\/(?!c0000000000000000000000000000001)[^/?]+/)
  expect(asked.some((line) => line.startsWith(`POST /artifacts/${id(2)}/docx/session`))).toBe(true)

  // a web report with none opens in the artifacts panel
  await page.goto('/files?tab=working')
  await page.getByRole('button', { name: '분기 성과 보고서 편집기에서 열기' }).click()
  await expect(page).toHaveURL(/\/artifacts/)
  await expect.poll(() => asked).toContain(`GET /artifacts/${id(4)}`)
  expect(unexpected).toEqual([])
})

test('on a phone 작업 문서 keeps each title whole and its controls under it @phone', async ({ page }, info) => {
  test.skip((page.viewportSize()?.width ?? 0) > 640, 'phone width')
  const { unexpected } = await fixture(page, info)
  await page.goto('/files?tab=working')
  await expect(rows(page)).toHaveCount(50)
  const row = rows(page).first()
  const title = row.getByRole('button', { name: '직원 연수 공문', exact: true })
  const open = row.getByRole('button', { name: '직원 연수 공문 편집기에서 열기' })
  await expect(open).toBeVisible()
  const [titleBox, openBox] = [await title.boundingBox(), await open.boundingBox()]
  expect(openBox!.y).toBeGreaterThan(titleBox!.y)
  const width = page.viewportSize()!.width
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await expect(page.getByLabel('정렬')).toBeVisible()
  expect(unexpected).toEqual([])
})
