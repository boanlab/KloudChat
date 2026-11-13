import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import deckFixture from './fixtures/presentation-deck.json' with { type: 'json' }
import { officeFixture } from './office-fixture'
import { viewport } from './project'
import { deckId, deckSession, panel, resultsFixture } from './results-fixture'

/**
 * The web slides work and look like the PowerPoint editor: the document header every panel
 * has, one toolbar row in the PowerPoint editor's order, numbered thumbnails with the slide
 * menu and drag to reorder, the speaker notes under the slide, the same keys. Hand edits stay in
 * the browser until 저장, across slides; undo covers the whole deck; a layout change keeps what
 * it cannot show; a phone reads only, as the PowerPoint editor does.
 */

test.use({ serviceWorkers: 'block' })

const SHOTS = process.env.SLIDES_UNIFY_SHOTS

const header = (page: Page) => panel(page).locator('header').first()
const editor = (page: Page) => panel(page).getByRole('region', { name: '웹 슬라이드 편집기' })
const toolbar = (page: Page) => panel(page).getByRole('toolbar', { name: '슬라이드 도구' })
const thumbs = (page: Page) => editor(page).locator('.kc-thumb')
const notes = (page: Page) => editor(page).getByRole('textbox', { name: '발표자 노트' })
const unsaved = (page: Page) => header(page).getByText('저장 안 됨')
const stageTitle = (page: Page) => editor(page).locator('.kc-canvas [contenteditable="true"]').first()

/** Every PATCH of the deck, as the server got it. */
function patches(page: Page) {
  const sent: { slides: { id: string; title: string; notes?: string; layout: string; bullets?: string[]; held?: unknown }[] }[] = []
  page.on('request', (request) => {
    if (request.method() === 'PATCH' && request.url().endsWith(`/api/artifacts/${deckId}`)) {
      sent.push((request.postDataJSON() as { data: (typeof sent)[number] }).data)
    }
  })
  return sent
}

async function open(page: Page) {
  await page.goto(`/s/${deckSession}`)
  await expect(panel(page)).toBeVisible()
  await expect(thumbs(page)).toHaveCount(4)
}

/** Types into the speaker notes of the slide shown and leaves them. */
async function writeNotes(page: Page, text: string) {
  await notes(page).fill(text)
  await notes(page).blur()
}

test('the toolbar, rail and notes read like the PowerPoint editor', async ({ page }, testInfo) => {
  test.skip(viewport(testInfo) === 'phone', 'desktop width')
  const recorded = await resultsFixture(page, testInfo)
  await open(page)
  // the header every document panel has, in its order
  const names = await header(page).getByRole('button').evaluateAll((buttons) =>
    buttons.map((b) => b.getAttribute('aria-label') ?? b.textContent?.trim() ?? ''))
  const order = ['저장', '내려받기', '이 아티팩트만 공유', '더보기']
  expect(names.filter((name) => order.includes(name))).toEqual(order)
  await expect(header(page).getByText('v1', { exact: true })).toBeVisible()
  await expect(header(page).getByRole('button', { name: '저장' })).toBeDisabled()

  // one row, in the PowerPoint editor's order and groups; at half a laptop screen the type
  // controls fold into menus, as the PowerPoint editor folds its paragraph controls
  const tools = () => toolbar(page).locator(':scope > button, :scope > .kc-menu > button').evaluateAll((buttons) =>
    buttons.map((b) => b.getAttribute('aria-label')))
  expect(await tools()).toEqual([
    '실행 취소', '다시 실행', '확대/축소', '레이아웃', '이미지', '표', '차트', '글꼴과 크기', '굵게', '기울임꼴',
    '밑줄', '텍스트 색상', '더보기', '디자인', '검토', '발표',
  ])
  const inRow = async () => {
    const bar = (await toolbar(page).boundingBox())!
    const boxes = await toolbar(page).locator('button:visible').evaluateAll((buttons) =>
      buttons.map((b) => b.getBoundingClientRect()).map((r) => ({ top: Math.round(r.top), right: r.right })))
    expect(Math.max(...boxes.map((b) => b.top)) - Math.min(...boxes.map((b) => b.top))).toBeLessThan(4)
    expect(Math.max(...boxes.map((b) => b.right))).toBeLessThanOrEqual(bar.x + bar.width + 1)
  }
  await inRow()
  await page.setViewportSize({ width: 2200, height: 1000 })
  await expect.poll(tools).toEqual([
    '실행 취소', '다시 실행', '확대/축소', '레이아웃', '이미지', '표', '차트', '글꼴', '글꼴 크기 줄이기',
    '글꼴 크기', '글꼴 크기 늘리기', '굵게', '기울임꼴', '밑줄', '텍스트 색상', '서식 지우기', '디자인', '검토', '발표',
  ])
  await inRow()
  await page.setViewportSize({ width: 1440, height: 900 })

  // numbered thumbnails, 「+ 새 슬라이드」, the notes of the slide shown
  await expect(thumbs(page).locator('.kc-num')).toHaveText(['1', '2', '3', '4'])
  await expect(editor(page).getByRole('button', { name: '새 슬라이드' })).toBeVisible()
  await thumbs(page).nth(1).click()
  await expect(notes(page)).toHaveValue(/^절차는 단계별로/)
  // the slide menu
  await thumbs(page).nth(1).click({ button: 'right' })
  const menu = page.getByRole('menu', { name: '슬라이드 메뉴' })
  await expect(menu.getByRole('menuitem')).toHaveText(['새 슬라이드', '슬라이드 복제', '위로 이동', '아래로 이동', '슬라이드 삭제'])
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)

  // 디자인 holds the theme, the accent colours, this slide's look and several slides at once
  await toolbar(page).getByRole('button', { name: '디자인' }).click()
  const design = page.getByRole('menu', { name: '디자인' })
  await expect(design).toContainText('테마')
  await expect(design.getByRole('group', { name: '강조색 고르기' })).toBeVisible()
  await expect(design.getByRole('menuitemradio', { name: /따뜻한/ })).toHaveAttribute('aria-checked', 'true')
  await expect(design.getByRole('button', { name: '여러 장 서식' })).toBeVisible()
  await page.keyboard.press('Escape')
  expect(recorded.unexpected).toEqual([])
})

test('slide menu, keys and deck-wide undo: nothing is saved until 저장', async ({ page }, testInfo) => {
  test.skip(viewport(testInfo) === 'phone', 'desktop width')
  await resultsFixture(page, testInfo)
  const sent = patches(page)
  await open(page)

  // 슬라이드 복제 from the menu: a fifth slide, unsaved
  await thumbs(page).nth(1).click({ button: 'right' })
  await page.getByRole('menuitem', { name: '슬라이드 복제' }).click()
  await expect(thumbs(page)).toHaveCount(5)
  await expect(unsaved(page)).toBeVisible()
  // a note on slide 1, then undo twice from the keyboard: both undone, deck-wide
  await thumbs(page).nth(0).click()
  await writeNotes(page, '첫 장 노트')
  await editor(page).locator('.kc-canvas').click({ position: { x: 4, y: 4 } })
  await page.keyboard.press('Control+z')
  await expect(notes(page)).toHaveValue('')
  await expect(toolbar(page).getByRole('button', { name: '실행 취소' })).toBeEnabled()
  await page.keyboard.press('Control+z')
  await expect(thumbs(page)).toHaveCount(4)
  await expect(unsaved(page)).toHaveCount(0)
  await page.keyboard.press('Control+y')
  await expect(thumbs(page)).toHaveCount(5)
  // Ctrl+D duplicates the slide shown; Delete in the rail removes it
  await page.keyboard.press('Control+d')
  await expect(thumbs(page)).toHaveCount(6)
  await thumbs(page).nth(1).click()
  await page.keyboard.press('Delete')
  await expect(thumbs(page)).toHaveCount(5)
  // 아래로 이동 moves the slide shown
  const first = await thumbs(page).nth(0).getAttribute('data-i')
  expect(first).toBe('0')
  await thumbs(page).nth(0).click({ button: 'right' })
  await page.getByRole('menuitem', { name: '아래로 이동' }).click()
  await expect(thumbs(page).nth(1)).toHaveAttribute('aria-current', 'true')
  // drag to reorder
  await thumbs(page).nth(4).dragTo(thumbs(page).nth(0))
  expect(sent).toHaveLength(0)

  // Ctrl+S saves the whole deck once, from the version the edits started from
  await page.keyboard.press('Control+s')
  await expect(unsaved(page)).toHaveCount(0)
  await expect(header(page).getByText('v2', { exact: true })).toBeVisible()
  expect(sent).toHaveLength(1)
  expect(sent[0].slides).toHaveLength(5)
  expect(sent[0].slides[0].title).toBe('요약')
})

test('switching slides keeps every unsaved edit, and one 저장 saves them all', async ({ page }, testInfo) => {
  test.skip(viewport(testInfo) === 'phone', 'desktop width')
  await resultsFixture(page, testInfo)
  const sent = patches(page)
  await open(page)
  await writeNotes(page, '표지 노트')
  // a title typed on the slide itself
  await thumbs(page).nth(3).click()
  await stageTitle(page).click()
  await page.keyboard.press('End')
  await page.keyboard.insertText(' (고침)')
  await expect(unsaved(page)).toBeVisible()
  // another slide, the same slide again, and back: nothing dropped, nothing asked
  await thumbs(page).nth(2).click()
  await thumbs(page).nth(2).click()
  await thumbs(page).nth(0).click()
  await expect(notes(page)).toHaveValue('표지 노트')
  await thumbs(page).nth(3).click()
  await expect(stageTitle(page)).toHaveText('요약 (고침)')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await header(page).getByRole('button', { name: '저장' }).click()
  await expect(unsaved(page)).toHaveCount(0)
  expect(sent).toHaveLength(1)
  expect(sent[0].slides[0].notes).toBe('표지 노트')
  expect(sent[0].slides[3].title).toBe('요약 (고침)')
})

test('굵게 on a selection on the slide, then 저장: the slide keeps it as rich text', async ({ page }, testInfo) => {
  test.skip(viewport(testInfo) === 'phone', 'desktop width')
  await resultsFixture(page, testInfo)
  const sent = patches(page)
  await open(page)
  await thumbs(page).nth(3).click()
  await expect(toolbar(page).getByRole('button', { name: '굵게' })).toBeDisabled()
  const item = editor(page).locator('.kc-canvas [contenteditable="true"]', { hasText: '신청서는 학생이 제출합니다.' }).last()
  await item.click()
  await page.keyboard.press('Home')
  await page.keyboard.press('Shift+End')
  await expect(toolbar(page).getByRole('button', { name: '굵게' })).toBeEnabled()
  await toolbar(page).getByRole('button', { name: '굵게' }).click()
  await expect(toolbar(page).getByRole('button', { name: '굵게' })).toHaveAttribute('aria-pressed', 'true')
  await expect(unsaved(page)).toBeVisible()
  await page.keyboard.press('Control+s')
  await expect(unsaved(page)).toHaveCount(0)
  expect(sent).toHaveLength(1)
  expect(JSON.stringify((sent[0].slides[3] as { richText?: unknown }).richText)).toMatch(/<(b|strong)>신청서는 학생이 제출합니다\.<\/(b|strong)>/)
})

test('a layout change keeps what the new layout cannot show, and saves only with 저장', async ({ page }, testInfo) => {
  test.skip(viewport(testInfo) === 'phone', 'desktop width')
  await resultsFixture(page, testInfo)
  const sent = patches(page)
  await open(page)
  await thumbs(page).nth(3).click()
  await toolbar(page).getByRole('button', { name: '레이아웃' }).click()
  await page.getByRole('menuitemradio', { name: '원형 차트' }).click()
  // a chart pattern with no chart: the three lines are kept aside, said so
  const held = editor(page).getByTestId('deck-held')
  await expect(held).toContainText('문장 3개')
  expect(sent).toHaveLength(0)
  // back to bullets: the lines come back
  await toolbar(page).getByRole('button', { name: '레이아웃' }).click()
  await page.getByRole('menuitemradio', { name: '글머리표' }).click()
  await expect(held).toHaveCount(0)
  await expect(editor(page).locator('.kc-canvas')).toContainText('장소는 운영진이 안내합니다.')
  // a table into 핵심 수치 and out again keeps every row
  await thumbs(page).nth(2).click()
  await toolbar(page).getByRole('button', { name: '레이아웃' }).click()
  await page.getByRole('menuitemradio', { name: '핵심 수치' }).click()
  await toolbar(page).getByRole('button', { name: '레이아웃' }).click()
  await page.getByRole('menuitemradio', { name: '원형 차트' }).click()
  await expect(held).toBeVisible()
  // 다음 장으로 옮기기: what was kept goes onto a new slide after this one
  await held.getByRole('button', { name: '다음 장으로 옮기기' }).click()
  await expect(thumbs(page)).toHaveCount(5)
  await header(page).getByRole('button', { name: '저장' }).click()
  await expect(unsaved(page)).toHaveCount(0)
  expect(sent).toHaveLength(1)
  const text = JSON.stringify(sent[0].slides)
  for (const word of ['신청 및 참석', '확인 및 안내', '신청서 작성', '정확한 확인']) expect(text).toContain(word)
  expect(sent[0].slides[3].held).toBeUndefined()
})

test('unsaved edits ask before the panel closes; a lost race shows the conflict notice', async ({ page }, testInfo) => {
  test.skip(viewport(testInfo) === 'phone', 'desktop width')
  await resultsFixture(page, testInfo)
  await open(page)
  await writeNotes(page, '닫기 전에')
  await header(page).getByRole('button', { name: '닫기' }).click()
  const dialog = page.getByRole('dialog', { name: '저장하지 않은 편집이 있습니다' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button')).toContainText(['취소', '버리기', '저장'])
  await dialog.getByRole('button', { name: '취소' }).click()
  await expect(panel(page)).toBeVisible()
  await expect(notes(page)).toHaveValue('닫기 전에')
  // an agent turn asks the same, before anything is sent
  await page.getByRole('textbox', { name: '프롬프트 입력' }).first().fill('3번 장을 표로 바꿔 줘')
  await page.keyboard.press('Enter')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button')).toContainText(['취소', '버리고 보내기', '저장하고 보내기'])
  await dialog.getByRole('button', { name: '취소' }).click()
  await expect(notes(page)).toHaveValue('닫기 전에')

  // someone saved v2 meanwhile: 저장 does not overwrite it
  await page.route(`**/api/artifacts/${deckId}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({ json: {
      id: deckId, title: deckFixture.title, kind: 'deck', version: 2, partial: false, sessionId: deckSession,
      projectId: null, createdAt: '2026-10-08T00:00:00.000Z', updatedAt: '2026-10-08T00:06:00.000Z',
      data: deckFixture.data,
    } })
  })
  await header(page).getByRole('button', { name: '저장' }).click()
  const status = panel(page).getByRole('status').filter({ hasText: '그사이 다른 판이 저장되어' })
  await expect(status).toBeVisible()
  await expect(status.getByRole('button', { name: '내 편집 내려받기' })).toBeVisible()
  await expect(status.getByRole('button', { name: '최신 판 불러오기' })).toBeVisible()
  await expect(unsaved(page)).toBeVisible()
})

test('a phone reads only, with the same note as the PowerPoint editor', { tag: '@phone' }, async ({ page }, testInfo) => {
  test.skip(viewport(testInfo) !== 'phone', 'phone width')
  await resultsFixture(page, testInfo)
  await open(page)
  await expect(panel(page).getByTestId('office-read-only'))
    .toHaveText('휴대폰에서는 읽기만 합니다. 고칠 내용은 대화에서 에이전트에게 부탁하세요.')
  await expect(toolbar(page)).toHaveCount(0)
  await expect(header(page).getByRole('button', { name: '저장' })).toHaveCount(0)
  await expect(notes(page)).toHaveAttribute('readonly', '')
  await expect(editor(page).locator('[contenteditable="true"]')).toHaveCount(0)
  await expect(editor(page).getByRole('button', { name: '새 슬라이드' })).toHaveCount(0)
  // the slides still step, and 발표 is in 더보기
  await thumbs(page).nth(1).click()
  await expect(notes(page)).toHaveValue(/^절차는 단계별로/)
  await header(page).getByRole('button', { name: '더보기' }).click()
  await expect(page.getByRole('menuitem', { name: '발표' })).toBeVisible()
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(page.viewportSize()!.width)
})

test('pictures: web slides beside the PowerPoint editor', { tag: '@phone' }, async ({ page }, testInfo) => {
  test.skip(!SHOTS, 'SLIDES_UNIFY_SHOTS names the folder for the pictures')
  const at = viewport(testInfo)
  await resultsFixture(page, testInfo)
  await open(page)
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${SHOTS}/web-slides-${at}.png` })
  if (at !== 'phone') {
    await thumbs(page).nth(1).click({ button: 'right' })
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/web-slides-menu-${at}.png` })
    await page.keyboard.press('Escape')
    await toolbar(page).getByRole('button', { name: '디자인' }).click()
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/web-slides-design-${at}.png` })
  }
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await page.context().unrouteAll({ behavior: 'ignoreErrors' })
  await officeFixture(page, {
    kind: 'pptx',
    file: readFileSync(new URL('./fixtures/pptx-plan.pptx', import.meta.url)),
    agentFile: readFileSync(new URL('./fixtures/pptx-plan.pptx', import.meta.url)),
    title: '2027년 사업 계획 보고',
  })
  await expect(page.locator('[data-pptx-panel] .kc-thumb')).toHaveCount(5, { timeout: 30_000 })
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${SHOTS}/powerpoint-${at}.png` })
})
