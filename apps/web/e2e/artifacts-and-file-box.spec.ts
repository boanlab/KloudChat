import { expect, test, type Page } from '@playwright/test'
import { chatId, composerFixture } from './composer-fixture'

/**
 * 아티팩트 holds HTML pages, code files, charts and diagrams; 파일함 holds reports, decks,
 * pictures, sound, video and uploads. Old document and picture links redirect to the 파일함.
 * Desktop, then 390px and 360px phones.
 */

const at = '2026-11-15T03:00:00.000Z'
const id = (seed: string) => seed.padEnd(32, '0')
const picture = id('f1')

const row = (key: string, kind: string, section: string, title: string, data: Record<string, unknown>,
  extra: Record<string, unknown> = {}) => ({
  id: id(key), kind, section, title, version: 1, data, sessionId: chatId, projectId: null,
  createdAt: at, updatedAt: at, partial: false, ...extra,
})

const gallery = [
  row('a1', 'html', 'html', '출석 체크 페이지', { kind: 'html', language: 'html', content: '<p>출석</p>' },
    { version: 3, updatedAt: '2026-11-15T05:00:00.000Z' }),
  row('a2', 'code', 'code', 'column_mean.py', { kind: 'code', language: 'python', content: 'print(1)' },
    { updatedAt: '2026-11-15T04:00:00.000Z' }),
  row('a3', 'chart', 'chart', '월별 활성 사용자', { kind: 'chart', chartType: 'bar', series: [] },
    { version: 2, updatedAt: '2026-11-14T03:00:00.000Z' }),
  row('a4', 'image', 'diagram', '수집·분석 흐름도', { kind: 'image', style: 'figure', source: 'graph TD; A-->B',
    src: `/api/files/${id('f9')}/content` }, { updatedAt: '2026-11-13T03:00:00.000Z' }),
]
// not on the 아티팩트 page: a web report and a generated picture
const report = row('d1', 'report', 'document', '주간 업무 보고', { kind: 'report', sections: [] })
const photo = row('m1', 'image', 'media', '고양이 사진', { kind: 'image', style: 'photo', src: `/api/files/${picture}/content` })

const file = (key: string, name: string, origin: string, extra: Record<string, unknown> = {}) => ({
  id: id(key), name, size: 20480, mime: name.endsWith('.png') ? 'image/png' : 'application/pdf', tokens: 10,
  projectId: null, sessionId: chatId, preview: '', error: null, origin, createdAt: at, ...extra,
})
const files = [
  file('u1', '운영체제_강의계획서.pdf', 'upload', { sessionId: null }),
  file('f1', '고양이 사진.png', 'generated'),
  file('f2', '회의 요약 낭독.mp3', 'generated', { mime: 'audio/mpeg' }),
]

/** The 아티팩트 page's and 작업 문서's own reads, as the server answers them. */
async function pages(page: Page) {
  const asked: URLSearchParams[] = []
  await page.route('**/api/artifacts/gallery*', (route) => {
    const params = new URL(route.request().url()).searchParams
    asked.push(params)
    const q = params.get('q') ?? ''
    const section = params.get('section')
    const named = gallery.filter((a) => a.title.includes(q))
    const counts = Object.fromEntries(['html', 'code', 'chart', 'diagram'].map((s) => [s, named.filter((a) => a.section === s).length]))
    const items = named.filter((a) => !section || a.section === section)
    const sort = params.get('sort') ?? 'updated'
    const up = params.get('dir') === 'asc'
    const key = (a: (typeof gallery)[number]) =>
      sort === 'title' ? a.title : sort === 'section' ? a.section : sort === 'version' ? String(a.version) : a.updatedAt
    items.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0) * (up ? 1 : -1))
    return route.fulfill({ json: { items, total: items.length, next: null, counts } })
  })
  await page.route('**/api/artifacts/working*', (route) => route.fulfill({ json: {
    items: [{ id: report.id, kind: 'report', title: report.title, version: 2, sessionId: chatId,
      sessionTitle: '지원 사업 공고 정리', projectId: null, createdAt: at, updatedAt: at }],
    total: 1, next: null, counts: { report: 1 } } }))
  for (const one of [...gallery, report, photo]) {
    await page.route(`**/api/artifacts/${one.id}`, (route) => route.fulfill({ json: one }))
  }
  await page.route(`**/api/files/${picture}`, (route) => route.fulfill({ json: files[1] }))
  return asked
}

/** Nothing on the page is wider than the screen. */
async function fitsTheScreen(page: Page) {
  const wide = await page.evaluate(() => document.scrollingElement!.scrollWidth - window.innerWidth)
  expect(wide).toBeLessThanOrEqual(0)
}

async function shot(page: Page, name: string) {
  const dir = process.env.ARTIFACTS_SPLIT_SHOTS
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true })
}

test('the 아티팩트 page lists pages, code files, charts and diagrams, by tab and by column', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info, { files })
  const asked = await pages(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/artifacts')
  const rows = page.getByTestId('artifact-row')
  await expect(rows).toHaveCount(4)
  await expect(page.getByRole('tablist').first().getByRole('tab'))
    .toHaveText([/^전체\s*4/, /^HTML\s*1/, /^코드 파일\s*1/, /^차트\s*1/, /^도식\s*1/])
  // newest edit first; each row says its kind, a code file its language too
  await expect(rows.first()).toContainText('출석 체크 페이지')
  await expect(rows.filter({ hasText: 'column_mean.py' })).toContainText('코드 파일python')
  await expect(rows.filter({ hasText: '수집·분석 흐름도' })).toContainText('도식')
  await shot(page, 'artifacts-desktop-1440')

  // a column heading sorts the server's whole list, as in the file box
  await page.getByRole('columnheader', { name: '제목' }).getByRole('button').click()
  await expect.poll(() => asked.at(-1)?.get('sort')).toBe('title')
  expect(asked.at(-1)?.get('dir')).toBe('asc')
  await expect(page).toHaveURL(/sort=title&dir=asc/)
  await expect(rows.first()).toContainText('column_mean.py')

  await page.getByRole('tab', { name: /^차트/ }).click()
  await expect.poll(() => asked.at(-1)?.get('section')).toBe('chart')
  await expect(rows).toHaveCount(1)
  await page.getByLabel('아티팩트 검색').fill('없는 제목')
  await expect(page.getByText('찾는 아티팩트가 없습니다')).toBeVisible()

  // 원래 대화로: the conversation, its artifact open beside it
  await page.getByLabel('아티팩트 검색').fill('')
  await page.getByRole('tab', { name: /^HTML/ }).click()
  await page.getByRole('button', { name: '출석 체크 페이지 원래 대화로' }).click()
  await expect(page).toHaveURL(new RegExp(`/s/${chatId}`))
  expect(unexpected).toEqual([])
})

test('an old link to a document or a picture on the 아티팩트 page goes to where it lives now', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info, { files })
  await pages(page)
  await page.goto(`/artifacts?open=${photo.id}`)
  await expect(page).toHaveURL(new RegExp(`/files\\?tab=made&open=${picture}`))
  await expect(page.getByRole('tab', { name: /^만든 파일/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('dialog', { name: '고양이 사진.png' })).toBeVisible()

  await page.goto(`/artifacts?open=${report.id}`)
  await expect(page).toHaveURL(new RegExp(`/s/${chatId}\\?artifact=${report.id}|/s/${chatId}$`))

  // a code file still opens on the page itself
  await page.goto(`/artifacts?open=${gallery[1].id}`)
  await expect(page.getByRole('dialog', { name: 'column_mean.py' })).toBeVisible()
  expect(unexpected).toEqual([])
})

test('파일함 keeps documents, generated files and uploads apart, with their counts', async ({ page }, info) => {
  const { unexpected } = await composerFixture(page, info, { files })
  await pages(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/files?tab=made')
  await expect(page.getByRole('tab', { name: /^만든 파일\s*2/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('file-row')).toHaveText([/고양이 사진\.png/, /회의 요약 낭독\.mp3/])
  // sound and pictures show their own icons, not the document one
  await expect(page.getByTestId('file-row').filter({ hasText: '낭독.mp3' }).locator('svg.lucide-file-audio')).toHaveCount(1)
  await expect(page.getByTestId('file-row').filter({ hasText: '고양이 사진.png' }).locator('svg.lucide-file-image')).toHaveCount(1)
  await expect(page.getByRole('tab', { name: /^올린 파일\s*1/ })).toBeVisible()
  await expect(page.getByRole('tab', { name: /^대화에서 만든 파일/ })).toHaveCount(0)
  await shot(page, 'files-made-desktop-1440')

  await page.getByRole('tab', { name: /^작업 문서/ }).click()
  await expect(page).toHaveURL(/tab=working/)
  await expect(page.getByTestId('working-doc-row')).toHaveText([/주간 업무 보고/])
  await expect(page.getByRole('tab', { name: /^작업 문서\s*1/ })).toBeVisible()
  await shot(page, 'files-working-desktop-1440')

  await page.getByRole('tab', { name: /^올린 파일/ }).click()
  await expect(page.getByTestId('file-row')).toHaveText([/운영체제_강의계획서\.pdf/])
  expect(unexpected).toEqual([])
})

for (const width of [390, 360]) {
  test(`both pages fit a ${width}px phone and sort without headings @phone`, async ({ page }, info) => {
    const { unexpected } = await composerFixture(page, info, { files })
    const asked = await pages(page)
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/artifacts')
    const rows = page.getByTestId('artifact-row')
    await expect(rows).toHaveCount(4)
    // the headings give way to a column and a direction
    await expect(page.getByRole('columnheader', { name: '제목' })).toBeHidden()
    const sort = page.getByTestId('phone-sort')
    await expect(sort).toBeVisible()
    await sort.getByLabel('정렬 기준').selectOption('title')
    await expect.poll(() => asked.at(-1)?.get('sort')).toBe('title')
    // the title keeps the row's width; its kind and version sit under it
    const cell = rows.filter({ hasText: '출석 체크 페이지' }).getByRole('cell').nth(1)
    expect((await cell.boundingBox())!.width).toBeGreaterThan(width * 0.55)
    await expect(rows.filter({ hasText: '출석 체크 페이지' })).toContainText('v3')
    await fitsTheScreen(page)
    await shot(page, `artifacts-phone-${width}`)

    await page.goto('/files?tab=made')
    await expect(page.getByTestId('file-row')).toHaveCount(2)
    await fitsTheScreen(page)
    await shot(page, `files-made-phone-${width}`)
    await page.getByRole('tab', { name: /^작업 문서/ }).click()
    await expect(page.getByTestId('working-doc-row')).toHaveCount(1)
    await fitsTheScreen(page)
    await shot(page, `files-working-phone-${width}`)
    expect(unexpected).toEqual([])
  })
}
