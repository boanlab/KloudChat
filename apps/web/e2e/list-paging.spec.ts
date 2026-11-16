import { expect, test } from '@playwright/test'
import { at, composerFixture } from './composer-fixture'

/**
 * Long lists page the same way everywhere: 25 rows a page, 「first–last / 전체 N건」 with 이전 ·
 * numbers · 다음, hidden while one page holds the list, back to page 1 on a new search.
 * The project list stands for the lists held in memory; the file box for the server-paged ones.
 */

const project = (n: number) => ({
  id: n.toString(16).padStart(32, '0'), name: `프로젝트 ${String(n).padStart(2, '0')}`, description: '', emoji: '📁',
  instructions: '', files: [], sessionIds: [], skillIds: [], designSystemId: null, renderTemplates: {},
  createdAt: at, updatedAt: new Date(Date.parse(at) - n * 60_000).toISOString(),
})

test('the project list pages 25 at a time and a search starts again at page 1', { tag: '@phone' }, async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info)
  const many = Array.from({ length: 60 }, (_, i) => project(i + 1))
  await page.route('**/api/projects', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: many }) : route.fallback())
  await page.goto('/projects')
  const rows = page.getByRole('checkbox', { name: /^프로젝트 \d+ 선택$/ })
  const pager = page.getByRole('navigation', { name: '프로젝트 목록 페이지' })
  await expect(rows).toHaveCount(25)
  await expect(pager).toContainText('1–25 / 전체 60건')

  await pager.getByRole('button', { name: '다음 페이지' }).click()
  await expect(pager).toContainText('26–50 / 전체 60건')
  await expect(rows).toHaveCount(25)
  await pager.getByRole('button', { name: '3쪽' }).click()
  await expect(rows).toHaveCount(10)
  await expect(pager.getByRole('button', { name: '다음 페이지' })).toBeDisabled()

  // a search goes back to page 1; one page holds the matches, so no pager
  await page.getByLabel('프로젝트 검색').fill('프로젝트 07')
  await expect(rows).toHaveCount(1)
  await expect(pager).toHaveCount(0)
  await page.getByLabel('프로젝트 검색').fill('')
  await expect(rows).toHaveCount(25)
  await expect(pager).toContainText('1–25 / 전체 60건')
  expect(unexpected).toEqual([])
})

test('the file box pages on the server by offset and keeps its page when a file is deleted', async ({ page }, info) => {
  const file = (i: number) => ({
    id: `p${String(i).padStart(31, '0')}`, name: `보고서_${String(i).padStart(2, '0')}.txt`, size: 100, mime: 'text/plain',
    tokens: 0, projectId: null, sessionId: null, preview: '', error: null, origin: 'upload', createdAt: at,
  })
  const files = Array.from({ length: 30 }, (_, i) => file(i))
  const { unexpected } = await composerFixture(page, info, { files })
  const asked: string[] = []
  page.on('request', (r) => new URL(r.url()).pathname === '/api/files/page' && asked.push(new URL(r.url()).search))
  await page.goto('/files')
  const rows = page.getByTestId('file-row')
  const pager = page.getByRole('navigation', { name: '파일 페이지' })
  await expect(rows).toHaveCount(25)
  await pager.getByRole('button', { name: '2쪽' }).click()
  await expect(rows).toHaveCount(5)
  expect(asked.some((q) => q.includes('offset=25') && q.includes('limit=25'))).toBe(true)
  // deleting a file on the last page keeps the page; emptying it falls back to the one before
  for (const name of ['보고서_25.txt', '보고서_26.txt', '보고서_27.txt', '보고서_28.txt', '보고서_29.txt']) {
    await page.getByRole('button', { name: `${name} 삭제` }).click()
    await page.getByRole('dialog').getByRole('button', { name: '삭제', exact: true }).click()
    await expect(page.getByText(name, { exact: true })).toHaveCount(0)
  }
  await expect(rows).toHaveCount(25)
  await expect(pager).toHaveCount(0)
  expect(unexpected).toEqual([])
})
