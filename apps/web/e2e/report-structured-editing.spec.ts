import { expect, test } from '@playwright/test'
import { E2E_ADMIN, openAndSeedReport } from './helpers'

async function enterPageEditor(page: import('@playwright/test').Page) {
  await page.getByRole('tab', { name: '홈', exact: true }).click()
  const home = page.getByRole('toolbar', { name: '홈' })
  const toPageView = home.getByRole('button', { name: '페이지뷰' })
  if (await toPageView.isVisible().catch(() => false)) await toPageView.click()
  const edit = home.getByRole('button', { name: '내용 편집' })
  if (await edit.isVisible().catch(() => false)) await edit.click()
  await expect(page.locator('.ProseMirror').first()).toBeVisible({ timeout: 30_000 })
}

async function saveReport(page: import('@playwright/test').Page) {
  const save = page.getByLabel('빠른 도구').getByRole('button', { name: '저장', exact: true })
  await save.click()
  await expect(save).toBeHidden()
}

test('문서 개요에서 절을 찾고 편집 위치로 바로 이동한다', async ({ page }) => {
  await openAndSeedReport(page, '개요 이동을 검증할 본문이다.')
  await enterPageEditor(page)

  // 개요는 접힌 채로 열린다 — 좁은 패널에서 224px 를 먼저 가져가면 문서가
  // 잘리기 때문이다. 손잡이로 편다.
  const outline = page.getByRole('navigation', { name: '문서 개요' })
  await expect(outline).toBeHidden()
  await page.getByRole('button', { name: '문서 개요' }).click()
  await expect(outline).toBeVisible()
  const destinations = outline.locator('ol button')
  expect(await destinations.count()).toBeGreaterThan(0)
  await destinations.first().click()
  await expect(destinations.first()).toHaveAttribute('aria-current', 'location')

  await page.getByRole('button', { name: '문서 개요' }).click()
  await expect(outline).toBeHidden()
  await page.getByRole('button', { name: '문서 개요' }).click()
  await expect(outline).toBeVisible()
})

test('커서 위치의 쪽 나누기를 표시·삭제·복원하고 저장한다', async ({ page }) => {
  const seeded = await openAndSeedReport(page, '첫 쪽에 남을 본문이다.')
  await enterPageEditor(page)

  const editor = page.locator('.ProseMirror').first()
  await editor.click()
  await page.keyboard.press('Control+End')
  await page.getByRole('button', { name: '쪽 나누기' }).click()
  const pageBreak = editor.locator('[data-page-break="true"]')
  await expect(pageBreak).toHaveCount(1)
  // Tiptap groups transactions that occur within 500ms into one undo unit.
  // A real click takes longer; keep insertion and deletion distinct here too.
  await page.waitForTimeout(600)

  // An atomic editor node: clicking and Delete removes exactly the break.
  await pageBreak.click()
  await page.keyboard.press('Delete')
  await expect(pageBreak).toHaveCount(0)
  await page.getByRole('button', { name: '실행 취소' }).click()
  await expect(pageBreak).toHaveCount(1)

  await saveReport(page)
  await page.reload()
  await enterPageEditor(page)
  await expect(page.locator('.ProseMirror').first().locator('[data-page-break="true"]')).toHaveCount(1)

  await page.getByRole('tab', { name: '홈', exact: true }).click()
  await page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '실제 페이지' }).click()
  const preview = page.getByLabel('실제 페이지 미리보기')
  await expect.poll(async () => Number(await preview.getAttribute('data-page-count') ?? 0), { timeout: 30_000 }).toBeGreaterThan(1)

  const stored = await page.evaluate(
    async ([admin, id]: [typeof E2E_ADMIN, string]) => {
      const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
      const auth = await login.json()
      const response = await fetch(`/api/artifacts/${id}`, { headers: { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` } })
      return (await response.json()).data.sections[0].content
    },
    [E2E_ADMIN, seeded.id] as [typeof E2E_ADMIN, string],
  )
  expect(stored).toContain('data-page-break="true"')
})

test('문서 전체에서 다음 찾기·현재 바꾸기·모두 바꾸기를 저장한다', async ({ page }) => {
  const seeded = await openAndSeedReport(page, '교체 대상과 교체 대상을 검토한다.')
  await page.evaluate(
    async ([admin, id]: [typeof E2E_ADMIN, string]) => {
      const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
      const auth = await login.json(); const headers = { 'content-type': 'application/json', Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
      const full = await (await fetch(`/api/artifacts/${id}`, { headers })).json(); const data = full.data ?? full
      data.sections.push({ id: 'find-second', heading: '추가 검토', content: '추가 교체 대상이다.', format: 'markdown', status: 'done' })
      await fetch(`/api/artifacts/${id}`, { method: 'PATCH', headers, body: JSON.stringify({ data }) })
    },
    [E2E_ADMIN, seeded.id] as [typeof E2E_ADMIN, string],
  )
  await page.reload()
  await enterPageEditor(page)

  await page.getByRole('button', { name: '찾기 및 바꾸기' }).click()
  await page.getByLabel('찾을 내용').fill('교체 대상')
  await page.getByLabel('바꿀 내용').fill('신규 대상')
  await page.getByRole('button', { name: '다음 찾기' }).click()
  await expect(page.getByRole('status')).toHaveText('3개 찾음')
  await page.getByRole('button', { name: '바꾸기', exact: true }).click()
  await page.getByRole('button', { name: '모두 바꾸기' }).click()
  await expect(page.getByRole('status')).toHaveText('2개 바꿈')

  await saveReport(page)
  const contents = await page.evaluate(
    async ([admin, id]: [typeof E2E_ADMIN, string]) => {
      const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
      const auth = await login.json(); const full = await (await fetch(`/api/artifacts/${id}`, { headers: { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` } })).json()
      return full.data.sections.map((section: { content: string }) => section.content).join(' ')
    },
    [E2E_ADMIN, seeded.id] as [typeof E2E_ADMIN, string],
  )
  expect(contents).not.toContain('교체 대상')
  expect(contents.match(/신규 대상/g)).toHaveLength(3)
})

test('선택한 보고서 서식을 저장하고 DOCX·PDF·HWPX로 내보낸다', async ({ page }) => {
  const seeded = await openAndSeedReport(page, '서식 보존 문장')
  await enterPageEditor(page)
  const paragraph = page.locator('.ProseMirror').first().locator('p').first()
  await paragraph.selectText()
  await page.getByRole('button', { name: '굵게' }).click()
  await page.getByRole('button', { name: '기울임' }).click()
  await page.getByLabel('글자 크기').selectOption('18')
  await paragraph.selectText()
  await page.getByLabel('글자색 빠른 선택').selectOption('#cc0000')
  await page.getByRole('button', { name: '가운데 맞춤' }).click()
  await page.getByLabel('줄간격').selectOption('1.5')
  await saveReport(page)

  const result = await page.evaluate(
    async ([admin, id]: [typeof E2E_ADMIN, string]) => {
      const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
      const auth = await login.json(); const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
      const full = await (await fetch(`/api/artifacts/${id}`, { headers })).json()
      const files = await Promise.all(['docx', 'pdf', 'hwpx'].map(async (format) => {
        const response = await fetch(`/api/artifacts/${id}/export?format=${format}`, { headers })
        return { format, status: response.status, size: (await response.blob()).size }
      }))
      return { content: full.data.sections[0].content, files }
    },
    [E2E_ADMIN, seeded.id] as [typeof E2E_ADMIN, string],
  )
  expect(result.content).toContain('<strong>')
  expect(result.content).toContain('<em>')
  expect(result.content).toContain('font-size: 18pt')
  expect(result.content).toContain('color: #cc0000')
  expect(result.content).toContain('text-align: center')
  expect(result.content).toContain('line-height: 1.5')
  expect(result.files.every((file: { status: number; size: number }) => file.status === 200 && file.size > 1_000)).toBeTruthy()
})

test('선택 문장에 검토 메모를 달고 해결·다시 열기 상태를 저장한다', async ({ page }) => {
  const seeded = await openAndSeedReport(page, '검토가 필요한 핵심 문장이다.')
  await enterPageEditor(page)
  const paragraph = page.locator('.ProseMirror').first().locator('p').first()
  await paragraph.selectText()
  await page.getByRole('button', { name: '선택한 문장에 메모' }).click()
  const comments = page.getByRole('complementary', { name: '검토 메모' })
  await expect(comments).toBeVisible()
  await page.getByLabel('메모 내용').fill('수치의 출처를 다시 확인하세요.')
  await page.getByRole('button', { name: '메모 추가' }).click()
  await expect(comments).toContainText('수치의 출처를 다시 확인하세요.')
  await expect(page.getByRole('button', { name: '검토 메모' })).toContainText('검토 1')
  await saveReport(page)

  await page.reload()
  await enterPageEditor(page)
  await page.getByRole('button', { name: '검토 메모' }).click()
  await expect(page.getByRole('complementary', { name: '검토 메모' })).toContainText('수치의 출처를 다시 확인하세요.')
  await page.getByRole('button', { name: '해결로 표시' }).click()
  await expect(page.getByRole('button', { name: '검토 메모' })).toContainText('검토 0')
  await page.getByRole('button', { name: '다시 열기' }).click()
  await expect(page.getByRole('button', { name: '검토 메모' })).toContainText('검토 1')
  await saveReport(page)

  const stored = await page.evaluate(
    async ([admin, id]: [typeof E2E_ADMIN, string]) => {
      const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
      const auth = await login.json(); const full = await (await fetch(`/api/artifacts/${id}`, { headers: { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` } })).json()
      return full.data.reviewComments
    },
    [E2E_ADMIN, seeded.id] as [typeof E2E_ADMIN, string],
  )
  expect(stored).toHaveLength(1)
  expect(stored[0]).toMatchObject({ quote: '검토가 필요한 핵심 문장이다.', body: '수치의 출처를 다시 확인하세요.', status: 'open' })
})

/**
 * The operations people expect after generation: cite at the caret, reshape a
 * table, and reuse a section. Assertions end at the stored artifact so a
 * toolbar that only changes the browser cannot pass.
 */
test('보고서에서 인용·표·절 구조를 직접 고치고 다시 열 수 있다', async ({ page }) => {
  test.setTimeout(180_000)
  const seeded = await openAndSeedReport(page, '시장 규모는 전년보다 증가했다.')

  await page.evaluate(
    async ([admin, id]: [typeof E2E_ADMIN, string]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: admin.email, password: admin.password }),
      })
      const auth = await login.json()
      const headers = {
        'content-type': 'application/json',
        Authorization: `Bearer ${auth.accessToken ?? auth.access_token}`,
      }
      const full = await (await fetch(`/api/artifacts/${id}`, { headers })).json()
      const data = full.data ?? full
      data.sources = [{
        id: 'editing-source-1', ordinal: 1, title: '산업 동향 원문', year: '2026',
        url: 'https://example.com/trend', origin: 'web', originLabel: '웹 검색',
      }]
      await fetch(`/api/artifacts/${id}`, {
        method: 'PATCH', headers, body: JSON.stringify({ data }),
      })
    },
    [E2E_ADMIN, seeded.id] as [typeof E2E_ADMIN, string],
  )
  await page.reload()

  await enterPageEditor(page)
  const editor = page.locator('.ProseMirror').first()
  await editor.click()

  await page.getByLabel('출처 인용 넣기').selectOption('1')
  await expect(editor).toContainText('[1]')

  await page.getByRole('button', { name: '표 넣기' }).click()
  await expect(editor.locator('table')).toHaveCount(1)
  await expect(editor.locator('tr')).toHaveCount(3)
  await page.getByRole('button', { name: '현재 행 삭제' }).click()
  await expect(editor.locator('tr')).toHaveCount(2)
  await expect(page.getByRole('button', { name: '현재 열 삭제' })).toBeVisible()
  await expect(page.getByRole('button', { name: '선택한 셀 병합' })).toBeVisible()
  await expect(page.getByRole('button', { name: '셀 나누기' })).toBeVisible()
  await expect(page.getByRole('button', { name: '첫 행을 머리글로 전환' })).toBeVisible()

  await saveReport(page)
  await page.reload()
  await enterPageEditor(page)
  await expect(page.locator('.ProseMirror').first()).toContainText('[1]')
  await expect(page.locator('.ProseMirror').first().locator('tr')).toHaveCount(2)

  // Page view's toggle says the destination in its visible text.
  await page.getByRole('tab', { name: '홈', exact: true }).click()
  await page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '실제 페이지' }).click()
  await page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).click()
  const sectionMenu = page.getByRole('button', { name: /절 편집/ }).first()
  await sectionMenu.click()
  await page.getByRole('menuitem', { name: '이 절 복제' }).click()
  await expect(page.getByRole('heading', { name: /사본$/ })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: /사본$/ })).toBeVisible()
})
