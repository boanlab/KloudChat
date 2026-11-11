import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { officeFixture, type OfficeOptions } from './office-fixture'

/**
 * The Word panel beside a chat, with the real folio editor: it loads the saved version,
 * saves a `.docx` from the version it loaded, gives way to the agent's versions, locks while
 * a turn runs, asks about unsaved edits before one starts, and runs under the app's CSP.
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
/** A line of the document as folio paints it. */
const line = (page: Page, text: string) => docxPanel(page).getByText(text).first()

async function handEdit(page: Page, at: string, typed: string) {
  const box = (await line(page, at).boundingBox())!
  await page.mouse.click(box.x + box.width - 2, box.y + box.height / 2)
  await page.keyboard.press('End')
  await page.keyboard.insertText(typed)
}

test('the saved file opens in folio and a hand edit saves a .docx as the next version', async ({ page }) => {
  const state = await fixture(page)
  await expect(line(page, '붙임  1. 연수 일정표 1부.')).toBeVisible({ timeout: 30_000 })
  const header = panel(page).locator('header')
  await expect(header.getByText('v1', { exact: true })).toBeVisible()
  await expect(header.getByRole('button', { name: '저장' })).toBeDisabled()
  // its own toolbar, in Korean
  await expect(docxPanel(page).getByRole('button', { name: /굵게/ }).first()).toBeVisible()

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
  await expect(docxPanel(page).getByRole('button', { name: /굵게/ })).toHaveCount(0)
  expect(state.problems).toEqual([])
})

test('home starts a chat with a blank Word document beside 「한글 문서」', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page, { fromHome: true })
  await expect(page.getByRole('group', { name: '만들 것' }).getByRole('button', { name: 'Word 문서' }))
    .toHaveCount(0)
  await page.getByRole('button', { name: 'Word 문서', exact: true }).click()
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
