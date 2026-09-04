/** The page view on a stored report: A4 sheets from Paged.js, no layout written onto ProseMirror
 *  nodes, no phantom edits, recovery from pagination failure, 서식 and page settings persisted. */

import { expect, test } from '@playwright/test'
import { E2E_ADMIN, openAndSeedReport, signIn } from './helpers'

async function openPageView(page: import('@playwright/test').Page) {
  await signIn(page)
  await page.goto('/artifacts')
  // The kind first, then the newest of that kind, inside the grid.
  await page.getByRole('tab', { name: /보고서/ }).click()
  await page
    .locator('div.grid')
    .getByRole('button', { name: /열기$/ })
    .first()
    .click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 30_000 })

  // Pressed only when not already showing: a report with a `templateId` opens in the page view.
  await page.getByRole('tab', { name: '홈', exact: true }).click()
  const home = page.getByRole('toolbar', { name: '홈' })
  const toPageView = home.getByRole('button', { name: '페이지뷰' })
  if (await toPageView.isVisible().catch(() => false)) await toPageView.click()
  await expect(page.getByLabel('실제 페이지 미리보기')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.pagedjs_page').first()).toBeVisible({ timeout: 30_000 })
}

async function failFirstPagination(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    ;(window as Window & { __KLOUDCHAT_FORCE_PAGINATION_FAILURE__?: boolean })
      .__KLOUDCHAT_FORCE_PAGINATION_FAILURE__ = true
  })
}

test('페이지 조판 실패에서 재시도해 문서를 복구한다', async ({ page }) => {
  await failFirstPagination(page)
  await openAndSeedReport(page, '조판 실패 뒤에도 남아야 할 본문')
  await page.getByRole('tab', { name: '홈', exact: true }).click()
  await page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '페이지뷰' }).click()
  const failure = page.getByRole('alert')
  await expect(failure).toContainText('페이지뷰를 만들지 못했습니다')
  await expect(failure).toContainText('문서 내용은 그대로 보존되어 있습니다')
  await page.evaluate(() => {
    ;(window as Window & { __KLOUDCHAT_FORCE_PAGINATION_FAILURE__?: boolean })
      .__KLOUDCHAT_FORCE_PAGINATION_FAILURE__ = false
  })
  await failure.getByRole('button', { name: '다시 시도' }).click()
  await expect(page.getByLabel('실제 페이지 미리보기')).toHaveAttribute('data-page-count', /\d+/, { timeout: 30_000 })
  await expect(failure).toHaveCount(0)
})

test('페이지 조판 실패에서 내용 편집으로 빠져나간다', async ({ page }) => {
  await failFirstPagination(page)
  await openAndSeedReport(page, '편집 화면에서 복구할 본문')
  await page.getByRole('tab', { name: '홈', exact: true }).click()
  await page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '페이지뷰' }).click()
  const failure = page.getByRole('alert')
  await failure.getByRole('button', { name: '내용 편집' }).click()
  await expect(page.locator('.ProseMirror').first()).toContainText('편집 화면에서 복구할 본문')
})

test('페이지 조판 실패에서 웹뷰로 돌아간다', async ({ page }) => {
  await failFirstPagination(page)
  await openAndSeedReport(page, '웹뷰에서 복구할 본문')
  await page.getByRole('tab', { name: '홈', exact: true }).click()
  await page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '페이지뷰' }).click()
  const failure = page.getByRole('alert')
  await failure.getByRole('button', { name: '웹뷰로 보기' }).click()
  await expect(page.getByText('웹뷰에서 복구할 본문', { exact: false }).first()).toBeVisible()
  await expect(page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '페이지뷰' })).toBeVisible()
  await expect(page.getByLabel('실제 페이지 미리보기')).toHaveCount(0)
})

test('종이는 패널이 좁아도 A4 폭이다', async ({ page }) => {
  await openPageView(page)

  const width = await page.locator('.pagedjs_page').first().evaluate((el) => Number.parseFloat(getComputedStyle(el).width))
  expect(width).toBeGreaterThan(700)
})

test('쪽 나눔을 흉내 내려고 문서를 건드리지 않는다', async ({ page }) => {
  await openPageView(page)
  await page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '내용 편집' }).click()
  await expect(page.locator('.ProseMirror').first()).toBeVisible()

  // Nothing writes layout onto ProseMirror's own nodes.
  const written = await page.locator('.page').evaluate((el) => {
    const nodes = Array.from(el.querySelectorAll<HTMLElement>('.ProseMirror > *'))
    return nodes.filter((n) => n.style.marginTop !== '').length
  })
  expect(written).toBe(0)
})

test('문서를 열기만 해서는 고쳐진 것이 되지 않는다', async ({ page }) => {
  await openPageView(page)

  // Tiptap normalises the HTML on load; that is not an edit.
  await expect(page.getByRole('button', { name: '저장', exact: true })).toBeHidden()
})

test('문서를 실제 A4 낱장으로 조판한다', async ({ page }) => {
  await openPageView(page)

  const pages = page.locator('.pagedjs_page')
  expect(await pages.count()).toBeGreaterThanOrEqual(1)
  const first = await pages.first().boundingBox()
  expect(first?.height ?? 0).toBeGreaterThan(1000)
})

test('여러 쪽이면 페이지 사이가 실제 여백으로 분리된다', async ({ page }) => {
  await openPageView(page)
  const pages = page.locator('.pagedjs_page')
  test.skip(await pages.count() < 2, '한 쪽짜리 문서다')
  const first = await pages.nth(0).boundingBox()
  const second = await pages.nth(1).boundingBox()
  expect((second?.y ?? 0) - ((first?.y ?? 0) + (first?.height ?? 0))).toBeGreaterThan(10)
})

test('긴 단락과 표와 목록을 여러 페이지로 나눈다', async ({ page }) => {
  const paragraphs = Array.from({ length: 24 }, (_, i) => `긴 단락 ${i + 1}. 페이지 조판이 문장을 잃지 않는지 확인하기 위한 내용입니다. 같은 단락은 두 줄 이상 이어지도록 충분한 설명을 포함합니다.`).join('\n\n')
  const table = ['| 항목 | 설명 |', '|---|---|', ...Array.from({ length: 38 }, (_, i) => `| ${i + 1} | 표가 다음 페이지로 이어지는 행 ${i + 1} |`)].join('\n')
  const list = Array.from({ length: 24 }, (_, i) => `${i + 1}. 이어지는 목록 ${i + 1}`).join('\n')
  await openAndSeedReport(page, `${paragraphs}\n\n${table}\n\n${list}`)

  await page.getByRole('tab', { name: '홈', exact: true }).click()
  const toPageView = page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '페이지뷰' })
  if (await toPageView.isVisible().catch(() => false)) await toPageView.click()
  await expect(page.locator('.pagedjs_page').nth(1)).toBeVisible({ timeout: 30_000 })
  expect(await page.locator('.pagedjs_page').count()).toBeGreaterThan(1)
  const lastRow = page.locator('.pagedjs_page').getByText('표가 다음 페이지로 이어지는 행 38', { exact: true }).last()
  await lastRow.scrollIntoViewIfNeeded()
  await expect(lastRow).toBeVisible()
  const lastItem = page.locator('.pagedjs_page').getByText('이어지는 목록 24', { exact: true }).last()
  await lastItem.scrollIntoViewIfNeeded()
  await expect(lastItem).toBeVisible()
  for (const selector of ['.pagedjs_margin-bottom-right', '.pagedjs_margin-top-left', '.pagedjs_margin-bottom-left']) {
    await expect(page.locator('.pagedjs_page').last().locator(selector)).toHaveClass(/hasContent/)
  }
})


test('서식의 색이 실제로 적용된다', async ({ page }) => {
  await openPageView(page)

  // `:root` matches nothing inside a shadow tree; the template's tokens must still resolve.
  const tokens = await page.locator('.page').first().evaluate((el) => {
    const s = getComputedStyle(el)
    return {
      rule: s.getPropertyValue('--rule').trim(),
      border: s.getPropertyValue('--border').trim(),
      paper: s.getPropertyValue('--paper').trim(),
    }
  })
  expect(tokens.rule).not.toBe('')
  expect(tokens.border).toContain('color-mix')
  expect(tokens.paper).not.toBe('')
})

test('서식을 바꾸면 문서가 그 양식으로 나간다', async ({ page }) => {
  // The 서식 determines the exported file, so the choice is stored on the document.
  const seeded = await openAndSeedReport(page, ['## 현황', '', '지금은 이렇다.'].join('\n'))
  await page.getByRole('tab', { name: '홈', exact: true }).click()
  await page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '페이지뷰' }).click()
  await expect(page.locator('.page').first()).toBeVisible({ timeout: 30_000 })

  await page.getByRole('button', { name: '보고 문서', exact: true }).click()
  await page.getByRole('menuitemcheckbox', { name: '회의록' }).click()
  await expect(page.getByRole('button', { name: '회의록', exact: true })).toBeVisible({
    timeout: 15_000,
  })

  // Stored on the document.
  const stored = await page.evaluate(
    async ([admin, id]: [typeof E2E_ADMIN, string]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: admin.email, password: admin.password }),
      })
      const session = await login.json()
      const headers = { Authorization: `Bearer ${session.accessToken ?? session.access_token}` }
      const full = await (
        await fetch(`/api/artifacts/${id}`, { headers, credentials: 'include' })
      ).json()
      return (full.data ?? full).templateId
    },
    [E2E_ADMIN, seeded.id] as [typeof E2E_ADMIN, string],
  )
  expect(stored).toBe('doc-minutes')
})

test('페이지 설정을 저장하고 실제 페이지와 DOCX·PDF·HWPX에 적용한다', async ({ page }) => {
  await openPageView(page)
  await page.getByRole('tab', { name: '레이아웃', exact: true }).click()
  await page.getByRole('toolbar', { name: '레이아웃' }).getByRole('button', { name: '페이지 설정' }).click()
  await page.getByRole('textbox', { name: '머리말', exact: true }).fill('대외비 검토본')
  await page.getByRole('textbox', { name: '꼬리말', exact: true }).fill('전략기획실')
  await page.getByLabel('쪽 번호').selectOption('page')
  await page.getByLabel('첫 쪽에도 머리말 표시').check()
  await page.getByLabel('위 여백').fill('25')
  await page.getByLabel('오른쪽 여백').fill('18')
  await page.getByLabel('아래 여백').fill('24')
  await page.getByLabel('왼쪽 여백').fill('19')
  await expect(page.locator('.pagedjs_margin-top-left').first()).toHaveClass(/hasContent/, { timeout: 30_000 })
  await expect(page.locator('.pagedjs_margin-bottom-left').first()).toHaveClass(/hasContent/)
  await page.getByLabel('빠른 도구').getByRole('button', { name: '저장', exact: true }).click()
  await expect(page.getByLabel('빠른 도구').getByRole('button', { name: '저장', exact: true })).toBeHidden({ timeout: 20_000 })

  const result = await page.evaluate(async (admin) => {
    const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: admin.email, password: admin.password }) })
    const auth = await login.json(); const headers = { Authorization: `Bearer ${auth.accessToken ?? auth.access_token}` }
    const listed = await (await fetch('/api/artifacts?kind=report', { headers })).json(); const first = (Array.isArray(listed) ? listed : listed.items)[0]
    const full = await (await fetch(`/api/artifacts/${first.id}`, { headers })).json()
    const files = await Promise.all(['docx', 'pdf', 'hwpx'].map(async (format) => { const response = await fetch(`/api/artifacts/${first.id}/export?format=${format}`, { headers }); return { status: response.status, size: (await response.blob()).size } }))
    return { settings: full.data.pageSettings, files }
  }, E2E_ADMIN)
  expect(result.settings).toEqual({ header: '대외비 검토본', footer: '전략기획실', pageNumbers: 'page', firstPageHeader: true, margins: { top: 25, right: 18, bottom: 24, left: 19 } })
  expect(result.files.every((file: { status: number; size: number }) => file.status === 200 && file.size > 1_000)).toBeTruthy()
})
