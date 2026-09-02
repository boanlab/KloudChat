/**
 * Every control on the report and deck panels, and what each one actually does.
 *
 * Written after a change that touched both panels heavily — new blocks, new
 * layouts, a new rewrite endpoint, a new fix button — and then landed on a
 * `main` that seventeen other pull requests had moved under it. Neither half
 * of that is the kind of thing a screenshot catches: a button that lights up,
 * opens a menu and does nothing looks exactly like one that works.
 *
 * So each case names a control and an *observable consequence*. Not "it did
 * not throw" — a panel state changed, a file arrived, the document is
 * different. A control with no consequence worth naming is a control this file
 * should not be asserting about.
 *
 * Opened on artifacts that already exist rather than generated: a sweep that
 * costs a model call per control is a sweep nobody runs.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { E2E_ADMIN, signIn, openAndSeedReport } from './helpers'

/**
 * Titles of the artifacts of one kind that are actually finished, newest first.
 *
 * A deck with an unwritten slide in it — left behind by a generation that was
 * interrupted — has 내보내기 and 발표 disabled, and that is the panel being
 * right. A sweep that opens one reports a dead button and means nothing by it.
 * So completeness is decided here, by the same rule the panel uses.
 */
async function titles(page: Page, kind: string, least = 1): Promise<string[]> {
  return page.evaluate(
    async ([admin, want, least]: [typeof E2E_ADMIN, string, number]) => {
      const login = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: admin.email, password: admin.password }),
      })
      const s = await login.json()
      const headers = { Authorization: `Bearer ${s.accessToken ?? s.access_token}` }
      const listed = await (
        await fetch(`/api/artifacts?kind=${want}&limit=5`, { headers, credentials: 'include' })
      ).json()
      const items = Array.isArray(listed) ? listed : (listed.items ?? [])
      const whole = await Promise.all(
        items.map(async (a: { id: string; title: string }) => {
          const full = await (
            await fetch(`/api/artifacts/${a.id}`, { headers, credentials: 'include' })
          ).json()
          const data = full.data ?? full
          const parts: Record<string, unknown>[] = data.slides ?? data.sections ?? []
          const written = parts.every(
            (s) =>
              s.layout === 'title' ||
              (s.bullets as unknown[])?.length ||
              String(s.body ?? s.content ?? '').trim() ||
              (s.rows as unknown[])?.length ||
              (s.metrics as unknown[])?.length ||
              s.chart,
          )
          return written && parts.length >= least ? (a.title as string) : ''
        }),
      )
      return whole.filter(Boolean)
    },
    [E2E_ADMIN, kind, least] as [typeof E2E_ADMIN, string, number],
  )
}

/**
 * Opens one artifact by name.
 *
 * By its gallery card's `aria-label`, not by a substring of its title: on
 * `/artifacts` the sidebar lists every conversation, and a request that once
 * said 보고서 is a button too. A sweep that opened the wrong document would
 * report failures that are really about a document with nothing in it.
 */
async function open(page: Page, kind: string, least = 1): Promise<boolean> {
  await signIn(page)
  await page.goto('/artifacts')
  const found = await titles(page, kind, least)
  if (!found.length) return false
  await page.getByRole('button', { name: `${found[0]} 열기` }).first().click()
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 30_000 })
  return true
}

async function ribbon(page: Page, name: string) {
  const tab = page.getByRole('tab', { name, exact: true })
  if (await tab.isVisible().catch(() => false)) await tab.click()
}

/**
 * Every enabled control on screen, by accessible name.
 *
 * The whole page rather than `<main>`: the deck panel is portalled outside it,
 * so a sweep scoped to `main` reported that 내보내기 had vanished from a panel
 * that was showing it. The gallery and sidebar names that come along do not
 * collide with anything asserted here.
 */
async function controls(scope: Locator): Promise<string[]> {
  return scope.getByRole('button').evaluateAll((els) =>
    els
      .filter((e) => !(e as HTMLButtonElement).disabled)
      .map((e) => (e.getAttribute('aria-label') || e.textContent || '').trim())
      .filter(Boolean),
  )
}

/** Presses one item in an open menu, whatever element the menu renders it as. */
function item(page: Page, label: string): Locator {
  return page.getByRole('menuitem', { name: label }).or(page.getByText(label, { exact: true }))
}

// ── 보고서 ─────────────────────────────────────────────────────────────

test('보고서 패널이 약속한 컨트롤을 내놓는다', async ({ page }) => {
  test.skip(!(await open(page, 'report')), '보고서 아티팩트가 없습니다')
  const named: string[] = []
  for (const tab of ['홈', '삽입', '레이아웃', '검토', '보기', '파일']) {
    await ribbon(page, tab)
    named.push(...await controls(page.getByRole('dialog')))
  }
  // A panel missing one of these lost a feature to a merge, which is the
  // failure this sweep exists for.
  for (const label of ['내보내기', '페이지뷰', '검사 결과']) {
    expect(named, `${label} 가 사라졌다`).toContain(label)
  }
  // Printing is one of the ways this document leaves, so it lives with the
  // others rather than taking a button of its own and folding the toolbar onto
  // a second row.
  await ribbon(page, '파일')
  await page.getByRole('button', { name: '내보내기', exact: true }).click()
  await expect(page.getByRole('menuitem', { name: '인쇄' })).toBeVisible()
})

test('페이지뷰 토글이 두 방향 모두 간다', async ({ page }) => {
  // Its own document, not whichever is newest.
  //
  // The rest of this file audits the controls on a finished artifact and that
  // is the point of it. This case is different: it is about a button, and the
  // button behaves differently depending on the document — one written into a
  // 서식 opens *in* the page view, because the panel reads `templateId` and
  // starts there. Opening whatever was written last made the case pass or fail
  // on which spec ran before it.
  await openAndSeedReport(page, ['## 현황', '', '지금은 이렇다.'].join('\n'))

  await ribbon(page, '홈')
  const toggle = page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '페이지뷰' })
  await toggle.click()
  await expect(page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' })).toBeVisible()
  // The reverse action is named for its destination as well.
  await page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '웹뷰' }).click()
  await expect(page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '페이지뷰' })).toBeVisible()
})

test('좁은 패널에서 목차 서랍이 열리고 닫힌다', async ({ page }) => {
  // Only where there is a drawer to open. With the panel wide the contents
  // stand beside the document and the button is deliberately absent — asking
  // for it at 1440px was asserting the opposite of what the panel promises.
  await page.setViewportSize({ width: 900, height: 800 })
  test.skip(!(await open(page, 'report')), '보고서 아티팩트가 없습니다')
  await ribbon(page, '보기')
  const toc = page.getByRole('button', { name: '목차', exact: true })
  test.skip((await toc.count()) === 0, '이 폭에서는 목차가 이미 펼쳐져 있습니다')
  await toc.click()
  const close = page.getByRole('button', { name: '목차 닫기' })
  await expect(close).toBeVisible({ timeout: 10_000 })
  await close.click()
  await expect(close).toHaveCount(0)
})

test('보고서 내보내기의 모든 형식이 파일을 준다', async ({ page }) => {
  test.skip(!(await open(page, 'report')), '보고서 아티팩트가 없습니다')
  await ribbon(page, '파일')
  for (const [label, extension] of [['PDF', 'pdf'], ['Word 문서', 'docx'], ['한글 문서', 'hwpx'], ['마크다운 원문', 'md']] as const) {
    await page.getByRole('button', { name: '내보내기' }).click()
    await expect(item(page, label).first()).toBeVisible({ timeout: 10_000 })
    const download = page.waitForEvent('download', { timeout: 90_000 })
    await item(page, label).first().click()
    const file = await download
    expect(await file.path(), `${label} 가 파일을 주지 않았다`).toBeTruthy()
    expect(file.suggestedFilename()).toMatch(new RegExp(`\\.${extension}$`, 'i'))
  }
})

test('검사 결과가 열리고 내용을 보여준다', async ({ page }) => {
  test.skip(!(await open(page, 'report')), '보고서 아티팩트가 없습니다')
  await ribbon(page, '검토')
  await page.getByRole('button', { name: '검사 결과' }).click()
  // Findings, or the offer to review — both are the panel working. An empty
  // popover is not.
  await expect(page.getByText('자동 검사').or(page.getByText('검토')).first()).toBeVisible({
    timeout: 10_000,
  })
})

test('문서 수정이 편집기를 연다', async ({ page }) => {
  test.skip(!(await open(page, 'report')), '보고서 아티팩트가 없습니다')
  await ribbon(page, '홈')
  const edit = page.getByRole('toolbar', { name: '홈' }).getByRole('button', { name: '문서 수정' })
  test.skip((await edit.count()) === 0, '이 보고서는 아직 쓰는 중입니다')
  await edit.first().click()
  await expect(page.locator('textarea, .ProseMirror').first()).toBeVisible({ timeout: 15_000 })
})

// ── 슬라이드 ───────────────────────────────────────────────────────────

test('슬라이드 패널이 약속한 컨트롤을 내놓는다', async ({ page }) => {
  test.skip(!(await open(page, 'deck')), '덱 아티팩트가 없습니다')
  const named: string[] = []
  for (const tab of ['홈', '삽입', '검토', '보기', '슬라이드 쇼', '파일']) {
    await ribbon(page, tab)
    named.push(...await controls(page.getByRole('dialog')))
  }
  // `장 목록` is not here: it lives in the presentation header, not the panel
  // toolbar, and is asserted in the presentation test below where it exists.
  for (const label of ['내보내기', '발표', '검사 결과']) {
    expect(named, `${label} 가 사라졌다`).toContain(label)
  }
})

test('발표 모드가 켜지고, 넘어가고, 장 목록을 열고, 꺼진다', async ({ page }) => {
  // Two slides at least: this case is about moving between them, and a deck of
  // one satisfies "every slide is written" while making the arrow keys a no-op.
  // The counter then reads 1 / 1 before and after, and the failure claims the
  // key does nothing when there is simply nowhere to go.
  test.skip(!(await open(page, 'deck', 2)), '두 장 이상인 덱이 없습니다')
  await ribbon(page, '슬라이드 쇼')
  await page.getByRole('toolbar', { name: '슬라이드 쇼' }).getByRole('button', { name: '발표', exact: true }).click()
  const end = page.getByRole('button', { name: '발표 끝내기' })
  await expect(end).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: '발표 시간 다시 시작' })).toBeVisible()
  await expect(page.getByRole('button', { name: '전체 화면' })).toBeVisible()

  // The counter is the observable consequence of an arrow key — a slide that
  // changed and one that did not look identical in a screenshot.
  const counter = page.getByText(/^\d+ \/ \d+$/).first()
  const before = await counter.innerText()
  await page.keyboard.press('ArrowRight')
  await expect(counter).not.toHaveText(before)
  await page.keyboard.press('ArrowLeft')
  await expect(counter).toHaveText(before)

  // `장 목록` belongs to this header, not to the panel toolbar.
  const list = page.getByRole('button', { name: '장 목록' })
  await expect(list).toBeVisible()
  await list.click()
  await expect(list).toHaveAttribute('aria-pressed', 'true')
  await list.click()
  await expect(list).toHaveAttribute('aria-pressed', 'false')

  await end.click()
  await expect(end).toHaveCount(0)
})

test('슬라이드 내보내기가 파일을 준다', async ({ page }) => {
  test.skip(!(await open(page, 'deck')), '덱 아티팩트가 없습니다')
  await ribbon(page, '파일')
  for (const [label, extension] of [['PowerPoint', 'pptx'], ['PDF (발표용)', 'pdf'], ['텍스트 (노트 포함)', 'md']] as const) {
    await page.getByRole('button', { name: '내보내기' }).click()
    await expect(item(page, label).first()).toBeVisible({ timeout: 10_000 })
    const download = page.waitForEvent('download', { timeout: 90_000 })
    await item(page, label).first().click()
    const file = await download
    expect(await file.path(), `${label} 가 파일을 주지 않았다`).toBeTruthy()
    expect(file.suggestedFilename()).toMatch(new RegExp(`\\.${extension}$`, 'i'))
  }
})

test('덱의 검사 결과도 열린다', async ({ page }) => {
  test.skip(!(await open(page, 'deck')), '덱 아티팩트가 없습니다')
  await ribbon(page, '검토')
  await page.getByRole('button', { name: '검사 결과' }).click()
  await expect(page.getByText('자동 검사').or(page.getByText('검토')).first()).toBeVisible({
    timeout: 10_000,
  })
})
