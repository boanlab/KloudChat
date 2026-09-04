/** The report surface after generation: title, hand edits, exports, version restore. Asserted on files and stored artifacts. */

import { readFileSync } from 'node:fs'
import { inflateRawSync } from 'node:zlib'
import { expect, test } from '@playwright/test'
import { approvePlan, ribbonTab, signIn } from './helpers'

// Retried: the content is model-written.
test.describe.configure({ retries: 2 })

/** The text of `Contents/section0.xml` inside an .hwpx. */
function hwpxText(bytes: Buffer): string {
  let at = 0
  while (at + 30 <= bytes.length && bytes.readUInt32LE(at) === 0x04034b50) {
    const method = bytes.readUInt16LE(at + 8)
    const compressed = bytes.readUInt32LE(at + 18)
    const nameLength = bytes.readUInt16LE(at + 26)
    const extraLength = bytes.readUInt16LE(at + 28)
    const name = bytes.subarray(at + 30, at + 30 + nameLength).toString()
    const start = at + 30 + nameLength + extraLength
    if (name === 'Contents/section0.xml') {
      const raw = bytes.subarray(start, start + compressed)
      const xml = (method === 0 ? raw : inflateRawSync(raw)).toString('utf-8')
      return [...xml.matchAll(/<hp:t>([\s\S]*?)<\/hp:t>/g)]
        .map((m) => m[1].replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&'))
        .join('\n')
    }
    at = start + compressed
  }
  throw new Error('section0.xml 을 찾지 못했습니다')
}

test.describe.configure({ mode: 'serial' })

const stamp = () => Math.random().toString(36).slice(2, 7)

/** The stored report, from the API. */
async function artifact(page: import('@playwright/test').Page, id: string) {
  return page.evaluate(async (artifactId) => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = await login.json()
    const r = await fetch(`/api/artifacts/${artifactId}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    return r.json()
  }, id)
}

/** Opens the newest report on its own surface and returns its artifact id. */
async function openNewestReport(page: import('@playwright/test').Page) {
  await page.goto('/artifacts')
  await page.getByRole('tab', { name: /^보고서/ }).click()
  await page.getByText('원본 작업 열기').first().click()
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 20_000 })
  await expect(page.getByRole('button', { name: '원문 편집' })).toBeVisible({ timeout: 20_000 })
  return page.evaluate(async () => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = await login.json()
    const rows = await (
      await fetch('/api/artifacts', { headers: { Authorization: `Bearer ${accessToken}` } })
    ).json()
    const list = Array.isArray(rows) ? rows : rows.items
    return list.find((a: { kind: string }) => a.kind === 'report').id as string
  })
}

/** Replaces the whole document through the editor and saves. */
async function rewrite(page: import('@playwright/test').Page, markdown: string) {
  // A freshly generated panel may still be drawing.
  await expect(page.getByRole('button', { name: '원문 편집' })).toBeVisible({ timeout: 60_000 })
  await page.getByRole('button', { name: '원문 편집' }).click()
  await page.getByLabel('문서 원본').fill(markdown)
  // Two buttons are named 저장; the editor's shortcut is unambiguous.
  await page.getByLabel('문서 원본').press('Control+Enter')
  await expect(page.getByLabel('문서 원본')).toBeHidden({ timeout: 20_000 })
}

test.beforeEach(async ({ page }) => {
  await signIn(page)
})

test('대학원생 — 만들어진 보고서의 제목이 내가 친 요청 문장이 아니다', async ({ page }) => {
  test.setTimeout(600_000)
  const request = '전이학습이 소량 데이터에서 왜 효과적인지 짧은 기술 검토 보고서.'

  await page.goto('/new/report')
  await page.getByLabel('프롬프트 입력').fill(request)
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect(page).toHaveURL(/\/s\/[0-9a-f]{32}/, { timeout: 30_000 })
  // Nothing is written until the plan is approved.
  await approvePlan(page, 480_000)
  // The section count is on the 목차 button in the 보기 tab.
  await expect(page.locator('[data-panel="artifact"]')).toBeVisible({ timeout: 180_000 })
  await ribbonTab(page, '보기')
  await expect(page.getByRole('button', { name: /목차 \d+\/[3-8]/ })).toBeVisible({
    timeout: 180_000,
  })
  await expect(page.getByLabel('중지')).toHaveCount(0, { timeout: 480_000 })

  const id = await page.evaluate(async () => {
    const login = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: 'e2e-personas@example.com',
        password: 'personas-playwright-pass',
      }),
    })
    const { accessToken } = await login.json()
    const rows = await (
      await fetch('/api/artifacts', { headers: { Authorization: `Bearer ${accessToken}` } })
    ).json()
    const list = Array.isArray(rows) ? rows : rows.items
    return list.find((a: { kind: string }) => a.kind === 'report').id as string
  })
  const stored = await artifact(page, id)

  // A cover line, not the sentence that was typed.
  expect(stored.title, '요청 문장이 그대로 제목이 되었다').not.toBe(request)
  expect(stored.title, '요청 문장의 앞부분을 잘라 쓴 제목').not.toBe(request.slice(0, 60))
  expect(stored.title.endsWith('.'), '제목에 마침표가 남았다').toBe(false)
  expect(stored.title.length).toBeGreaterThan(3)
})

test('사무직 — 보고서를 한글 문서로 받으면 한글이 여는 파일이다', async ({ page }) => {
  test.setTimeout(180_000)
  await openNewestReport(page)

  await ribbonTab(page, '파일')
  await page.getByRole('button', { name: '내보내기', exact: true }).click()
  const download = page.waitForEvent('download', { timeout: 60_000 })
  await page.getByRole('menuitem', { name: '한글 문서' }).click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/\.hwpx$/)

  const bytes = readFileSync((await file.path())!)
  // OWPML: the first zip entry is an uncompressed `mimetype`, checked by byte offset.
  expect(bytes.subarray(0, 4).toString('binary')).toBe('PK')
  expect(bytes.readUInt16LE(8), 'mimetype 이 압축되어 있다').toBe(0)
  expect(bytes.subarray(30, 38).toString()).toBe('mimetype')
  expect(bytes.subarray(38, 57).toString()).toBe('application/hwp+zip')
  const text = bytes.toString('binary')
  for (const part of ['Contents/header.xml', 'Contents/section0.xml', 'META-INF/container.xml']) {
    expect(text, `${part} 누락`).toContain(part)
  }
})

test('연구직 — 번호를 매긴 절차가 내보낸 문서에서 번호를 유지한다', async ({ page }) => {
  test.setTimeout(180_000)
  await openNewestReport(page)
  const title = `실험 절차 ${stamp()}`

  await rewrite(
    page,
    `# ${title}\n\n## 측정 절차\n\n아래 순서로 수행한다.\n\n1. 시료를 준비한다\n1. 온도를 25℃ 로 맞춘다\n1. 3회 반복 측정한다\n\n## 재시작 절차\n\n8. 여덟 번째 단계\n9. 아홉 번째 단계\n10. 열 번째 단계\n`,
  )

  await ribbonTab(page, '파일')
  await page.getByRole('button', { name: '내보내기', exact: true }).click()
  const hwpxDownload = page.waitForEvent('download', { timeout: 60_000 })
  await page.getByRole('menuitem', { name: '한글 문서' }).click()
  const submitted = hwpxText(readFileSync((await (await hwpxDownload).path())!))

  // `1. 1. 1.` renders as 1, 2, 3; the exported document says so in ink.
  expect(submitted).toContain('1. 시료를 준비한다')
  expect(submitted).toContain('2. 온도를 25℃ 로 맞춘다')
  expect(submitted).toContain('3. 3회 반복 측정한다')
  expect(submitted).toContain('8. 여덟 번째 단계')
  expect(submitted).toContain('10. 열 번째 단계')
  expect(submitted).toContain(title)

  // The Markdown export is the source, `1. 1. 1.` included.
  await ribbonTab(page, '파일')
  await page.getByRole('button', { name: '내보내기', exact: true }).click()
  const mdDownload = page.waitForEvent('download', { timeout: 60_000 })
  await page.getByRole('menuitem', { name: '마크다운 원문' }).click()
  const md = readFileSync((await (await mdDownload).path())!, 'utf-8')
  expect(md).toContain('1. 온도를 25℃ 로 맞춘다')
  expect(md).toContain(`# ${title}`)
})

test('개발직 — 코드 블록 안의 ## 이 절을 쪼개지 않는다', async ({ page }) => {
  test.setTimeout(180_000)
  const id = await openNewestReport(page)

  await rewrite(
    page,
    '# 마크다운 작성 지침\n\n## 문법 예시\n\n아래처럼 쓴다.\n\n```md\n## 이건 예시일 뿐이다\n### 이것도\n```\n\n설명 문단.\n\n## 적용 범위\n\n전 부서에 적용한다.\n',
  )

  const stored = await artifact(page, id)
  const headings = stored.data.sections.map((s: { heading: string }) => s.heading)
  expect(headings, '코드 블록이 절로 잘못 나뉘었다').toEqual(['문법 예시', '적용 범위'])
  expect(stored.data.sections[0].content).toContain('## 이건 예시일 뿐이다')
})

test('학부생 — 잘못 저장한 보고서를 버전 기록으로 되돌린다', async ({ page }) => {
  test.setTimeout(180_000)
  const id = await openNewestReport(page)
  const before = await artifact(page, id)

  await rewrite(page, '# 실수로 날린 보고서\n\n## 남은 절\n\n본문을 통째로 지웠다.\n')
  const wrecked = await artifact(page, id)
  expect(wrecked.data.sections).toHaveLength(1)

  await ribbonTab(page, '검토')
  await page.getByRole('button', { name: '버전 기록' }).click()
  const restoreButton = page.getByRole('button', { name: `v${before.version} 로 되돌리기` })
  await expect(restoreButton).toBeVisible({ timeout: 20_000 })
  await restoreButton.click()

  await expect
    .poll(async () => (await artifact(page, id)).data.sections.length, { timeout: 20_000 })
    .toBe(before.data.sections.length)

  const restored = await artifact(page, id)
  expect(restored.data.sections.map((s: { heading: string }) => s.heading)).toEqual(
    before.data.sections.map((s: { heading: string }) => s.heading),
  )
  // Restoring is an edit of its own.
  expect(restored.version).toBeGreaterThan(wrecked.version)
})
