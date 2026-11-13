import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { officeFixture, type OfficeOptions } from './office-fixture'

/**
 * The PowerPoint panel beside a chat, with the real slide editor (pptx-react-viewer under the
 * app's own chrome): it loads the saved version, saves a `.pptx` from the version it loaded
 * (the header's 저장 or Ctrl+S), gives way to the agent's versions, locks while a turn runs,
 * asks about unsaved edits before one starts, and runs under the app's CSP.
 */

test.use({ serviceWorkers: 'block' })

const deck = readFileSync(new URL('./fixtures/pptx-plan.pptx', import.meta.url))
const filled = readFileSync(new URL('./fixtures/pptx-plan-filled.pptx', import.meta.url))
const blank = readFileSync(new URL('./fixtures/pptx-blank.pptx', import.meta.url))

function fixture(page: Page, options: Partial<OfficeOptions> = {}) {
  return officeFixture(page, {
    kind: 'pptx', file: deck, agentFile: filled, title: '2027년 사업 계획 보고', ...options,
  })
}

const panel = (page: Page) => page.locator('[data-panel="artifact"]')
const pptxPanel = (page: Page) => page.locator('[data-pptx-panel]')
const toolbar = (page: Page) => pptxPanel(page).getByRole('toolbar', { name: '슬라이드 도구' })
const thumbs = (page: Page) => pptxPanel(page).locator('.kc-thumb')
const canvas = (page: Page) => pptxPanel(page).locator('.kc-canvas')
const notes = (page: Page) => pptxPanel(page).getByRole('textbox', { name: '발표자 노트' })

async function opened(page: Page) {
  await expect(thumbs(page)).toHaveCount(5, { timeout: 30_000 })
  await expect(canvas(page).getByText('2027년 사업 계획 보고').first()).toBeVisible()
}

/** A hand edit: a line typed into the speaker notes of slide 2. */
async function handEdit(page: Page, typed: string) {
  await thumbs(page).nth(1).click()
  await expect(notes(page)).toHaveValue(/^수치는 \[기준일\] 기준\./)
  await notes(page).click()
  await page.keyboard.press('Control+End')
  await page.keyboard.insertText(typed)
  // the notes reach the deck when the person leaves them
  await canvas(page).click({ position: { x: 4, y: 4 } })
}

test('the saved deck opens with the toolbar, thumbnails and notes, and a hand edit saves the next version', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page)
  await opened(page)
  const header = panel(page).locator('header')
  await expect(header.getByText('v1', { exact: true })).toBeVisible()
  await expect(header.getByRole('button', { name: '저장' })).toBeDisabled()
  for (const name of ['실행 취소', '텍스트 상자', '이미지', '굵게', '기울임꼴', '밑줄']) {
    await expect(toolbar(page).getByRole('button', { name, exact: true })).toBeVisible()
  }
  await expect(toolbar(page).getByRole('button', { name: '표' })).toBeVisible()

  await handEdit(page, ' 손으로 고침')
  await expect(header.getByText('저장 안 됨')).toBeVisible()
  await header.getByRole('button', { name: '저장' }).click()
  await expect(header.getByText('v2', { exact: true })).toBeVisible()
  await expect(header.getByText('저장 안 됨')).toBeHidden()
  expect(state.saves).toEqual([{ expected: '1', size: expect.any(Number) }])
  expect(state.heads).toEqual(['PK'])
  // a later edit is an edit again
  await handEdit(page, ' 둘째')
  await expect(header.getByText('저장 안 됨')).toBeVisible()
  expect(state.unexpected).toEqual([])
  expect(state.problems).toEqual([])
  expect(state.csp).toEqual([])
})

test("the editor's own utility classes leave the rest of the app's text sizes alone", async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  await fixture(page)
  await opened(page)
  // the editor's stylesheet has its own `.text-sm`, `.text-xs`, …, read from variables it sets
  // on its own elements only: outside the editor they would size text from the parent instead
  const sizes = await page.evaluate(() => {
    const root = parseFloat(getComputedStyle(document.documentElement).fontSize)
    const read = (cls: string) => {
      const probe = document.createElement('span')
      probe.className = cls
      probe.textContent = '가'
      document.body.append(probe)
      const size = parseFloat(getComputedStyle(probe).fontSize) / root
      probe.remove()
      return size
    }
    return { sm: read('text-sm'), xs: read('text-xs') }
  })
  expect(sizes).toEqual({ sm: 0.75, xs: 0.6875 })
})

test('Ctrl+S in the editor saves as the header 저장 does', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page)
  await opened(page)
  await handEdit(page, ' 단축키')
  const header = panel(page).locator('header')
  await expect(header.getByText('저장 안 됨')).toBeVisible()
  await thumbs(page).nth(1).click()
  await page.keyboard.press('Control+S')
  await expect(header.getByText('v2', { exact: true })).toBeVisible()
  await expect(header.getByText('저장 안 됨')).toBeHidden()
  expect(state.saves).toEqual([{ expected: '1', size: expect.any(Number) }])
})

test('a new table is headed in Korean, and an element has its right-click menu and Ctrl+C / Ctrl+V', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  await fixture(page)
  await opened(page)
  await thumbs(page).nth(4).click()
  await toolbar(page).getByRole('button', { name: '표' }).click()
  await toolbar(page).locator('[data-testid=table] button[data-r="2"][data-c="3"]').click()
  await expect(canvas(page).getByText('항목 1', { exact: true })).toBeVisible()
  await expect(canvas(page).getByText('Header 1')).toHaveCount(0)

  // two text boxes do not land on one spot
  await toolbar(page).getByRole('button', { name: '텍스트 상자' }).click()
  await toolbar(page).getByRole('button', { name: '텍스트 상자' }).click()
  const boxes = canvas(page).locator('[data-element-id]')
  const places = await boxes.evaluateAll((els) => els.map((el) => {
    const box = el.getBoundingClientRect()
    return `${Math.round(box.left)},${Math.round(box.top)}`
  }))
  expect(new Set(places).size).toBe(places.length)

  await thumbs(page).nth(3).click()
  const title = canvas(page).getByText('다음 단계').first()
  await title.click({ button: 'right' })
  const menu = page.getByRole('menu', { name: '도형 메뉴' })
  for (const name of ['잘라내기', '복사', '붙여넣기', '도형 복제', '앞으로 가져오기', '뒤로 보내기', '삭제']) {
    await expect(menu.getByRole('menuitem', { name, exact: true })).toBeVisible()
  }
  await menu.getByRole('menuitem', { name: '복사' }).click()
  await expect(menu).toBeHidden()
  const before = await boxes.count()
  await page.keyboard.press('Control+V')
  await expect(boxes).toHaveCount(before + 1)
  await expect(canvas(page).getByText('다음 단계', { exact: true })).toHaveCount(2)
  await expect(panel(page).locator('header').getByText('저장 안 됨')).toBeVisible()
})

test('the editor locks while the agent works, then shows its version', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  let release!: () => void
  const holdTurn = new Promise<void>((resolve) => (release = resolve))
  const state = await fixture(page, { holdTurn })
  await opened(page)
  // the person is on slide 2 when the agent's version comes in: it opens there
  await thumbs(page).nth(1).click()

  // a PowerPoint order stays here, in the editor, rather than going to the slides screen
  await page.getByRole('textbox', { name: '프롬프트 입력' }).first()
    .fill('사업 계획 발표 자료를 PowerPoint로 채워 주세요')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('pptx-agent-lock')).toBeVisible()
  release()

  await expect(page.getByTestId('pptx-agent-lock')).toBeHidden()
  await expect(panel(page).locator('header').getByText('v2', { exact: true })).toBeVisible()
  // the version loaded in place: the caret stays in the chat, the slide where it was
  await expect(page.getByRole('textbox', { name: '프롬프트 입력' }).first()).toBeFocused()
  await expect(thumbs(page).nth(1)).toHaveClass(/\bon\b/)
  await expect(canvas(page).getByText('추진 배경과 목표').first()).toBeVisible()
  expect(state.sent).toHaveLength(1)
  expect(state.unexpected).toEqual([])
})

test('unsaved hand edits are saved before the agent turn when the person says so', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page)
  await opened(page)
  await handEdit(page, ' 보내기 전')
  await expect(panel(page).locator('header').getByText('저장 안 됨')).toBeVisible()

  await page.getByRole('textbox', { name: '프롬프트 입력' }).first().fill('부서명은 경영기획본부입니다')
  await page.keyboard.press('Enter')
  const ask = page.getByRole('dialog', { name: '저장하지 않은 편집이 있습니다' })
  await expect(ask).toBeVisible()
  expect(state.sent).toHaveLength(0)
  await ask.getByRole('button', { name: '저장하고 보내기' }).click()
  await expect.poll(() => state.sent.length).toBe(1)
  expect(state.saves).toEqual([{ expected: '1', size: expect.any(Number) }])
  await expect(panel(page).locator('header').getByText('v3', { exact: true })).toBeVisible()
})

test('on a phone the deck opens to read, without a save button or a toolbar @phone', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) > 640, 'phone width only')
  const state = await fixture(page)
  await opened(page)
  const header = panel(page).locator('header')
  await expect(header.getByRole('button', { name: '저장' })).toHaveCount(0)
  await expect(header.getByRole('button', { name: '내려받기' })).toBeEnabled()
  await expect(toolbar(page)).toHaveCount(0)
  await expect(pptxPanel(page).locator('.kc-add')).toHaveCount(0)
  await thumbs(page).nth(1).click()
  await expect(notes(page)).toHaveAttribute('readonly', '')
  expect(state.problems).toEqual([])
  expect(state.csp).toEqual([])
})

test('home starts a chat with a blank PowerPoint deck beside 「한글 문서」 and 「Word 문서」', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page, { fromHome: true, file: blank })
  await expect(page.getByRole('group', { name: '만들 것' }).getByRole('button', { name: 'PowerPoint' }))
    .toHaveCount(0)
  await page.getByRole('button', { name: 'PowerPoint', exact: true }).click()
  await expect(page).toHaveURL(/\/s\/[0-9a-z_-]+/)
  await expect(pptxPanel(page)).toBeVisible()
  expect(state.created).toEqual([{ sessionId: expect.any(String), title: '새 PowerPoint 문서' }])
  // the blank title slide says where its title and subtitle go
  await expect(thumbs(page)).toHaveCount(1, { timeout: 30_000 })
  const prompts = await pptxPanel(page).locator('style').innerText()
  expect(prompts).toContain('제목을 입력하세요')
  expect(prompts).toContain('부제목을 입력하세요')
  // and an empty title place takes a double click, though it has no text to click on
  const title = canvas(page).locator('[data-element-id]').first()
  await title.dblclick()
  await page.keyboard.type('2027년 사업 계획')
  await page.keyboard.press('Escape')
  await expect(canvas(page)).toContainText('2027년 사업 계획')
  await expect(panel(page).locator('header').getByText('저장 안 됨')).toBeVisible()
  expect(state.unexpected).toEqual([])
})

test('an attached .pptx opens in the PowerPoint editor beside the chat', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page, { attachment: { id: 'file-1', name: '2-business-deck.pptx' } })
  await page.getByRole('button', { name: 'PowerPoint로 열기' }).click()
  await opened(page)
  expect(state.created).toEqual([{ sessionId: expect.any(String), fileId: 'file-1' }])
  expect(state.csp).toEqual([])
})
