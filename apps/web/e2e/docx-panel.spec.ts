import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { strFromU8, unzipSync } from 'fflate'
import { officeFixture, type OfficeOptions } from './office-fixture'

/**
 * The Word panel beside a chat, with the real open-docx-editor: it loads the saved version,
 * saves a `.docx` from the version it loaded (the header's 저장 or Ctrl+S), gives way to the
 * agent's versions, locks while a turn runs, asks about unsaved edits before one starts, and
 * runs under the app's CSP.
 */

test.use({ serviceWorkers: 'block' })

const docx = readFileSync(new URL('./fixtures/word-official.docx', import.meta.url))
const filled = readFileSync(new URL('./fixtures/word-official-filled.docx', import.meta.url))

function fixture(page: Page, options: Partial<OfficeOptions> = {}) {
  return officeFixture(page, {
    kind: 'docx', file: docx, agentFile: filled, title: '직원 연수 참석 요청', ...options,
  })
}

const panel = (page: Page) => page.locator('[data-panel="artifact"]')
const docxPanel = (page: Page) => page.locator('[data-docx-panel]')
/** A line of the document's body as the editor lays it out. */
const line = (page: Page, text: string) =>
  docxPanel(page).locator('.odx-content').getByText(text).first()

async function handEdit(page: Page, at: string, typed: string) {
  const box = (await line(page, at).boundingBox())!
  await page.mouse.click(box.x + box.width - 2, box.y + box.height / 2)
  await page.keyboard.press('End')
  await page.keyboard.insertText(typed)
}

test('the saved file opens in the editor and a hand edit saves a .docx as the next version', async ({ page }) => {
  const state = await fixture(page)
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  const header = panel(page).locator('header')
  await expect(header.getByText('v1', { exact: true })).toBeVisible()
  await expect(header.getByRole('button', { name: '저장' })).toBeDisabled()
  // Word's ribbon, in the app's language: its tabs, and Home's font, size and bold in the side panel's width
  const ribbon = docxPanel(page).locator('.odx-toolbar')
  for (const tab of ['파일', '홈', '삽입', '레이아웃', '참조', '검토', '보기']) {
    await expect(ribbon.getByRole('tab', { name: tab, exact: true })).toBeVisible()
  }
  await expect(ribbon.getByRole('combobox', { name: '글꼴', exact: true })).toBeVisible()
  await expect(ribbon.getByRole('combobox', { name: '글꼴 크기' })).toBeVisible()
  await expect(ribbon.getByRole('button', { name: /^굵게/ })).toBeVisible()

  await handEdit(page, '붙임  1. 연수 일정표 1부.', ' 손으로 고침')
  await expect(header.getByText('저장 안 됨')).toBeVisible()
  await header.getByRole('button', { name: '저장' }).click()
  await expect(header.getByText('v2', { exact: true })).toBeVisible()
  await expect(header.getByText('저장 안 됨')).toBeHidden()
  expect(state.saves).toEqual([{ expected: '1', size: expect.any(Number) }])
  expect(state.heads).toEqual(['PK'])
  expect(state.unexpected).toEqual([])
  expect(state.problems).toEqual([])
  expect(state.csp).toEqual([])
})

test('a save that lost to a newer version says so and loads the latest on request', async ({ page }) => {
  await fixture(page, { conflict: true })
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  await handEdit(page, '붙임  1. 연수 일정표 1부.', ' 늦은 편집')
  const header = panel(page).locator('header')
  await expect(header.getByText('저장 안 됨')).toBeVisible()
  await header.getByRole('button', { name: '저장' }).click()
  const notice = panel(page).getByRole('status')
  await expect(notice).toContainText('그사이 다른 판이 저장되어 저장하지 않았습니다.')
  await notice.getByRole('button', { name: '최신 판 불러오기' }).click()
  await expect(notice).toBeHidden()
  await expect(docxPanel(page).getByText('늦은 편집')).toHaveCount(0)
  await expect(header.getByText('저장 안 됨')).toBeHidden()
})

test('Ctrl+S in the editor saves as the header 저장 does', async ({ page }) => {
  const state = await fixture(page)
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  await handEdit(page, '붙임  1. 연수 일정표 1부.', ' 단축키')
  const header = panel(page).locator('header')
  await expect(header.getByText('저장 안 됨')).toBeVisible()
  await page.keyboard.press('Control+S')
  await expect(header.getByText('v2', { exact: true })).toBeVisible()
  await expect(header.getByText('저장 안 됨')).toBeHidden()
  expect(state.saves).toEqual([{ expected: '1', size: expect.any(Number) }])
})

test('printing copies the pages laid out for print out of the app tree, then drops them', async ({ page }) => {
  await fixture(page)
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  // File > Export and Print open the print dialog; the app tree itself is not printed
  await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')))
  const copy = page.locator('body > [data-print-docx]')
  await expect(copy).toContainText('붙임  1. 연수 일정표 1부.')
  await page.emulateMedia({ media: 'print' })
  await expect(copy).toBeVisible()
  await expect(page.locator('#root')).toBeHidden()
  await page.emulateMedia({ media: 'screen' })
  await page.evaluate(() => window.dispatchEvent(new Event('afterprint')))
  await expect(copy).toHaveCount(0)
})

test('the editor locks while the agent works, then shows its version', async ({ page }) => {
  let release!: () => void
  const holdTurn = new Promise<void>((resolve) => (release = resolve))
  const state = await fixture(page, { holdTurn })
  await expect(line(page, '제목  [연수명] 참석 요청')).toBeVisible({ timeout: 30_000 })

  // a Word order stays here, in the editor, rather than going to the report screen
  await page.getByRole('textbox', { name: '프롬프트 입력' }).first()
    .fill('직원 연수 참석을 요청하는 공문을 Word로 작성해 주세요')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('docx-agent-lock')).toBeVisible()
  release()

  await expect(page.getByTestId('docx-agent-lock')).toBeHidden()
  await expect(panel(page).locator('header').getByText('v2', { exact: true })).toBeVisible()
  await expect(line(page, '제목  교원 직무연수 참석 요청')).toBeVisible()
  // the version loaded in place: the caret stays in the chat
  await expect(docxPanel(page).locator('.odx-content')).not.toBeFocused()
  await expect(page.getByText('· 문서 확인 2회 · 문서 편집 2회')).toBeVisible()
  expect(state.sent).toHaveLength(1)
  expect(state.unexpected).toEqual([])
})

test('unsaved hand edits are saved before the agent turn when the person says so', async ({ page }) => {
  const state = await fixture(page)
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  await handEdit(page, '붙임  1. 연수 일정표 1부.', ' 보내기 전')
  await expect(panel(page).locator('header').getByText('저장 안 됨')).toBeVisible()

  await page.getByRole('textbox', { name: '프롬프트 입력' }).first().fill('장소는 본관 강당입니다')
  await page.keyboard.press('Enter')
  const ask = page.getByRole('dialog', { name: '저장하지 않은 편집이 있습니다' })
  await expect(ask).toBeVisible()
  expect(state.sent).toHaveLength(0)
  await ask.getByRole('button', { name: '저장하고 보내기' }).click()
  await expect.poll(() => state.sent.length).toBe(1)
  expect(state.saves).toEqual([{ expected: '1', size: expect.any(Number) }])
  await expect(panel(page).locator('header').getByText('v3', { exact: true })).toBeVisible()
})

test('on a phone the document opens to read, without a save button or a toolbar @phone', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) > 640, 'phone width only')
  const state = await fixture(page)
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  const header = panel(page).locator('header')
  await expect(header.getByRole('button', { name: '저장' })).toHaveCount(0)
  await expect(header.getByRole('button', { name: '내려받기' })).toBeEnabled()
  await expect(docxPanel(page).locator('.odx-toolbar')).toBeHidden()
  await expect(docxPanel(page).locator('.odx-content')).toHaveAttribute('contenteditable', 'false')
  expect(state.problems).toEqual([])
})

test('home opens a blank Word document from the report surface\'s Word pick', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page, { fromHome: true })
  await expect(page.getByRole('button', { name: 'Word 문서', exact: true })).toHaveCount(0)
  await page.getByRole('group', { name: '만들 것' }).getByRole('button', { name: '보고서' }).click()
  await page.getByRole('group', { name: '결과 형식' }).getByRole('button', { name: 'Word', exact: true }).click()
  await page.getByRole('button', { name: '빈 문서로 시작' }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-z_-]+/)
  await expect(docxPanel(page)).toBeVisible()
  expect(state.created).toEqual([{ sessionId: expect.any(String), title: '새 Word 문서' }])
  expect(state.unexpected).toEqual([])
})

test('an attached .docx opens in the Word editor beside the chat', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page, { attachment: { id: 'file-1', name: '2-gongmun.docx' } })
  await page.getByRole('button', { name: 'Word로 열기' }).click()
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  expect(state.created).toEqual([{ sessionId: expect.any(String), fileId: 'file-1' }])
  expect(state.csp).toEqual([])
})

test('the ribbon speaks the app language: its tabs, groups and find bar in Korean', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  await fixture(page)
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  const ribbon = docxPanel(page).locator('.odx-toolbar')
  await expect(ribbon.getByRole('tablist', { name: '리본 탭' })).toBeVisible()
  await expect(ribbon.getByRole('button', { name: /^기울임꼴/ })).toBeVisible()
  // Ctrl+F opens the find bar with its own words (Find itself is behind 「명령 더 보기」 at this width)
  await line(page, '붙임  1. 연수 일정표 1부.').click()
  await page.keyboard.press('Control+f')
  await expect(ribbon.getByPlaceholder('찾기')).toBeVisible()
  await expect(ribbon.getByText('단어 단위로')).toBeVisible()
  await ribbon.getByRole('tab', { name: '검토', exact: true }).click()
  await expect(ribbon.getByRole('button', { name: /새 메모/ }).first()).toBeVisible()
})

test('with the app in English the ribbon is in English', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  await page.addInitScript(() => localStorage.setItem('kchat-lang', 'en'))
  await fixture(page)
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  const ribbon = docxPanel(page).locator('.odx-toolbar')
  await expect(ribbon.getByRole('tab', { name: 'Home', exact: true })).toBeVisible()
  await expect(ribbon.getByRole('combobox', { name: 'Font', exact: true })).toBeVisible()
  await expect(ribbon.getByRole('button', { name: /^Bold/ })).toBeVisible()
})

test('Korean: typed through an input method, broken between words, set in a Korean font saved as its East Asian font', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page)
  const at = '3. 장소는 [장소]으로 합니다.'
  await expect(line(page, at)).toBeVisible({ timeout: 30_000 })
  // a Korean document: its lines break between words (어절), as Word's do
  await expect(docxPanel(page).locator('.odx-page').first()).toHaveClass(/odx-ko/)
  const box = (await line(page, at).boundingBox())!
  await page.mouse.click(box.x + box.width - 2, box.y + box.height / 2)
  await page.keyboard.press('End')
  // an input method composing syllable by syllable, as a Korean keyboard does
  const cdp = await page.context().newCDPSession(page)
  for (const syllable of ['대', '한', '민', '국']) {
    await cdp.send('Input.imeSetComposition', { text: syllable, selectionStart: 1, selectionEnd: 1 })
    await cdp.send('Input.insertText', { text: syllable })
  }
  await expect(line(page, '합니다.대한민국')).toBeVisible()
  await page.keyboard.insertText(` ${'대한민국의 주권은 국민에게 있고 모든 권력은 국민으로부터 나온다. '.repeat(4)}`)
  const para = docxPanel(page).locator('.odx-content p').filter({ hasText: '대한민국의 주권은' })
  const lines = await para.evaluate((p) => {
    // where each laid-out line starts, as the character before it
    const before: string[] = []
    let text = ''
    let top: number | null = null
    const walk = document.createTreeWalker(p, NodeFilter.SHOW_TEXT)
    for (let n = walk.nextNode() as Text | null; n; n = walk.nextNode() as Text | null) {
      for (let i = 0; i < n.data.length; i += 1) {
        const r = document.createRange()
        r.setStart(n, i)
        r.setEnd(n, i + 1)
        const rect = r.getClientRects()[0]
        if (rect && n.data[i].trim()) {
          if (top !== null && rect.top > top + 4) before.push(text.slice(-1))
          top = rect.top
        }
        text += n.data[i]
      }
    }
    return before
  })
  expect(lines.length).toBeGreaterThan(1)
  expect(lines.filter((c) => c.trim() !== '')).toEqual([]) // every line starts after a space
  // Home > 글꼴: a Korean font is saved as the text's East Asian font too, one w:rFonts per run
  await para.click({ clickCount: 3 })
  await docxPanel(page).locator('.odx-toolbar').getByRole('combobox', { name: '글꼴', exact: true }).selectOption('굴림')
  await panel(page).locator('header').getByRole('button', { name: '저장' }).click()
  await expect.poll(() => state.files.length).toBe(1)
  const xml = strFromU8(unzipSync(new Uint8Array(state.files[0]))['word/document.xml'])
  const saved = xml.split('</w:p>').find((p) => p.includes('대한민국의 주권은'))!
  const runs = saved.match(/<w:r(?: [^>]*)?><w:rPr>[\s\S]*?<\/w:rPr>/g) ?? [] // (the runs', not the paragraph mark's)
  expect(runs.length).toBeGreaterThan(0)
  for (const rPr of runs) {
    expect(rPr.match(/<w:rFonts\b/g)).toHaveLength(1)
    expect(rPr).toMatch(/<w:rFonts w:hint="eastAsia" w:ascii="굴림" w:hAnsi="굴림" w:eastAsia="굴림"/)
  }
  expect(state.problems).toEqual([])
})
