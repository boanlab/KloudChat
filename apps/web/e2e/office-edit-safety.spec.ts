import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { artifactId, officeFixture, sessionId, type OfficeOptions } from './office-fixture'

/**
 * Unsaved hand edits in an office editor are never dropped without a word: closing the panel,
 * another page, a reload or a deploy's reload asks to save or drop them; what is typed while a
 * save runs stays unsaved; HWP reads only on a phone, as Word and PowerPoint do, and a window
 * narrowed over unsaved edits keeps 저장; a failed start or load offers a retry; HWP saves on
 * Ctrl+S. Office documents share, open read only on the shared page and the artifacts page,
 * and an attachment reopens the copy it made rather than making another.
 */

test.use({ serviceWorkers: 'block' })

const hwpx = readFileSync(new URL('./fixtures/hwp-official.hwpx', import.meta.url))
const docx = readFileSync(new URL('./fixtures/word-official.docx', import.meta.url))
const pptx = readFileSync(new URL('./fixtures/pptx-plan.pptx', import.meta.url))
const studio = readFileSync(new URL('./fixtures/fake-rhwp.html', import.meta.url), 'utf8')

const FILES = { hwp: hwpx, docx, pptx } as const

async function fixture(page: Page, kind: keyof typeof FILES, options: Partial<OfficeOptions> = {}) {
  await page.route('**/rhwp/**', (route) => route.fulfill({ contentType: 'text/html', body: studio }))
  return officeFixture(page, {
    kind, file: FILES[kind], agentFile: FILES[kind], title: '직원 연수 참석 요청', ...options,
  })
}

const desktopOnly = (page: Page) =>
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')

const panel = (page: Page) => page.locator('[data-panel="artifact"]')
const header = (page: Page) => panel(page).locator('header')
const asked = (page: Page) => page.getByRole('dialog', { name: '저장하지 않은 편집이 있습니다' })
const studioFrame = (page: Page) => page.frames().find((f) => f.url().includes('/rhwp/'))!
const hwpOpened = (page: Page) =>
  expect(page.frameLocator('[data-hwp-panel] iframe').locator('#doc'))
    .toHaveText(`document.hwpx ${hwpx.length}`)
const hwpEdit = (page: Page, text: string) =>
  studioFrame(page).evaluate(
    (value) => (window as unknown as { fakeStudio: { edit: (t: string) => void } }).fakeStudio.edit(value),
    text,
  )

/** Word: the fixture's last line, and text typed at its end. */
const WORD_LINE = '붙임  1. 연수 일정표 1부.'
const wordLine = (page: Page) =>
  page.locator('[data-docx-panel] .odx-content').getByText(WORD_LINE).first()
async function wordEdit(page: Page, typed: string) {
  const box = (await wordLine(page).boundingBox())!
  await page.mouse.click(box.x + box.width - 2, box.y + box.height / 2)
  await page.keyboard.press('End')
  await page.keyboard.insertText(typed)
}

test.describe('leaving unsaved edits', () => {
  test('closing the panel asks: cancel keeps the edits, 저장 saves and closes', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'hwp')
    await hwpOpened(page)
    await hwpEdit(page, '닫기 전 편집')
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()

    await header(page).getByRole('button', { name: '닫기' }).click()
    await expect(asked(page)).toContainText('이 문서를 떠나면 저장하지 않은 편집이 사라집니다.')
    await asked(page).getByRole('button', { name: '취소' }).click()
    await expect(asked(page)).toBeHidden()
    await expect(panel(page)).toBeVisible()
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()

    await header(page).getByRole('button', { name: '닫기' }).click()
    await asked(page).getByRole('button', { name: '저장', exact: true }).click()
    await expect(panel(page)).toBeHidden()
    expect(state.saves).toEqual([{ expected: '1', size: expect.any(Number) }])
    expect(state.unexpected).toEqual([])
  })

  test('closing the panel and dropping the edits saves nothing', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'docx')
    await expect(wordLine(page)).toBeVisible({ timeout: 30_000 })
    await wordEdit(page, ' 버릴 편집')
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
    await header(page).getByRole('button', { name: '닫기' }).click()
    await asked(page).getByRole('button', { name: '버리기' }).click()
    await expect(panel(page)).toBeHidden()
    expect(state.saves).toEqual([])
  })

  test('another page from the sidebar waits for the answer, and goes once saved', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'hwp')
    await hwpOpened(page)
    await hwpEdit(page, '이동 전 편집')
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()

    await page.getByRole('link', { name: '아티팩트' }).first().click()
    await expect(asked(page)).toBeVisible()
    await asked(page).getByRole('button', { name: '취소' }).click()
    await expect(page).toHaveURL(new RegExp(`/s/${sessionId}`))
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()

    await page.getByRole('link', { name: '아티팩트' }).first().click()
    await asked(page).getByRole('button', { name: '저장', exact: true }).click()
    await expect(page).toHaveURL(/\/artifacts$/)
    expect(state.saves).toEqual([{ expected: '1', size: expect.any(Number) }])
  })

  test('a tab close or reload over unsaved edits asks the browser to confirm', async ({ page }) => {
    desktopOnly(page)
    await fixture(page, 'docx')
    await expect(wordLine(page)).toBeVisible({ timeout: 30_000 })
    await wordEdit(page, ' 닫기 전')
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
    const dialog = page.waitForEvent('dialog')
    await page.close({ runBeforeUnload: true })
    const shown = await dialog
    expect(shown.type()).toBe('beforeunload')
    await shown.dismiss()
  })

  test("a deploy's reload asks first rather than reloading over the edits", async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'hwp')
    await hwpOpened(page)
    await hwpEdit(page, '배포 중 편집')
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
    await page.evaluate(() => {
      (window as unknown as { stayed: boolean }).stayed = true
      window.dispatchEvent(new Event('vite:preloadError', { cancelable: true }))
    })
    await expect(asked(page)).toContainText('새 버전을 쓰려면 페이지를 새로 고쳐야 합니다.')
    await asked(page).getByRole('button', { name: '취소' }).click()
    // not reloaded: the page's own mark is still there, and so are the edits
    expect(await page.evaluate(() => (window as unknown as { stayed?: boolean }).stayed)).toBe(true)
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
    expect(state.saves).toEqual([])
  })

  test('discarding before a turn sends nothing when the saved file cannot be loaded back', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'hwp', { failLoads: [2] })
    await hwpOpened(page)
    await hwpEdit(page, '보내기 전 편집')
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
    await page.getByRole('textbox').first().fill('장소는 중앙홀')
    await page.keyboard.press('Enter')
    await asked(page).getByRole('button', { name: '버리고 보내기' }).click()
    await expect(panel(page).getByRole('status')).toContainText('문서를 불러오지 못했습니다.')
    await page.waitForTimeout(500)
    expect(state.sent).toHaveLength(0)
  })
})

test.describe('saves', () => {
  test('a Word edit typed while a save runs stays unsaved, and the next save takes it', async ({ page }) => {
    desktopOnly(page)
    let release!: () => void
    const holdSave = new Promise<void>((resolve) => (release = resolve))
    const state = await fixture(page, 'docx', { holdSave })
    await expect(wordLine(page)).toBeVisible({ timeout: 30_000 })
    await wordEdit(page, ' 먼저')
    await header(page).getByRole('button', { name: '저장' }).click()
    // the file is on its way: what is typed now is not in it
    await expect.poll(() => state.received.length).toBe(1)
    await wordEdit(page, ' 저장 중에')
    release()
    await expect(header(page).getByText('v2', { exact: true })).toBeVisible()
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
    await header(page).getByRole('button', { name: '저장' }).click()
    await expect(header(page).getByText('v3', { exact: true })).toBeVisible()
    await expect(header(page).getByText('저장 안 됨')).toBeHidden()
    expect(state.saves.map((s) => s.expected)).toEqual(['1', '2'])
  })

  test('an HWP edit made while a save runs stays unsaved', async ({ page }) => {
    desktopOnly(page)
    let release!: () => void
    const holdSave = new Promise<void>((resolve) => (release = resolve))
    const state = await fixture(page, 'hwp', { holdSave })
    await hwpOpened(page)
    await hwpEdit(page, '첫 편집')
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
    await header(page).getByRole('button', { name: '저장' }).click()
    await expect.poll(() => state.received.length).toBe(1)
    await hwpEdit(page, '저장 중 편집')
    release()
    await expect(header(page).getByText('v2', { exact: true })).toBeVisible()
    await page.waitForTimeout(2000) // past a poll of the editor's state
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
  })

  test('a PowerPoint edit made while a save runs stays unsaved', async ({ page }) => {
    desktopOnly(page)
    let release!: () => void
    const holdSave = new Promise<void>((resolve) => (release = resolve))
    const state = await fixture(page, 'pptx', { holdSave })
    const thumbs = page.locator('[data-pptx-panel] .kc-thumb')
    const notes = page.locator('[data-pptx-panel]').getByRole('textbox', { name: '발표자 노트' })
    const canvas = page.locator('[data-pptx-panel] .kc-canvas')
    await expect(thumbs).toHaveCount(5, { timeout: 30_000 })
    const note = async (typed: string) => {
      await notes.click()
      await page.keyboard.press('Control+End')
      await page.keyboard.insertText(typed)
      await canvas.click({ position: { x: 4, y: 4 } })
    }
    await thumbs.nth(1).click()
    await note(' 먼저')
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
    await header(page).getByRole('button', { name: '저장' }).click()
    await expect.poll(() => state.received.length).toBe(1)
    await note(' 저장 중에')
    release()
    await expect(header(page).getByText('v2', { exact: true })).toBeVisible()
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
  })

  test('HWP saves on Ctrl+S in the editor', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'hwp')
    await hwpOpened(page)
    await hwpEdit(page, '단축키 저장')
    await page.frameLocator('[data-hwp-panel] iframe').locator('#doc').click()
    await page.keyboard.press('Control+s')
    await expect(header(page).getByText('v2', { exact: true })).toBeVisible()
    expect(state.saves).toEqual([{ expected: '1', size: 4 + Buffer.byteLength('단축키 저장') }])
  })

  test('in English the version is saved as 「Saved in the editor」', async ({ page }) => {
    desktopOnly(page)
    await page.addInitScript(() => localStorage.setItem('kchat-lang', 'en'))
    const state = await fixture(page, 'hwp')
    await hwpOpened(page)
    await hwpEdit(page, 'English')
    await expect(header(page).getByText('Not saved')).toBeVisible()
    await header(page).getByRole('button', { name: 'Save', exact: true }).click()
    await expect.poll(() => state.summaries).toEqual(['Saved in the editor'])
  })
})

test.describe('phone and retries', () => {
  test('on a phone HWP reads only: its pages drawn, the editor out of reach @phone', async ({ page }) => {
    test.skip((page.viewportSize()?.width ?? 0) > 640, 'phone width only')
    const state = await fixture(page, 'hwp')
    await expect(panel(page).locator('[data-hwp-page="1"] svg'))
      .toContainText(`document.hwpx 1쪽 ${hwpx.length}`)
    await expect(header(page).getByRole('button', { name: '저장' })).toHaveCount(0)
    await expect(panel(page).getByTestId('office-read-only'))
      .toHaveText('휴대폰에서는 읽기만 합니다. 고칠 내용은 대화에서 에이전트에게 부탁하세요.')
    expect(await panel(page).locator('[data-hwp-panel] iframe').evaluate((f) => (f as HTMLElement).inert))
      .toBe(true)
    // what the drawing carried that could run is gone
    await expect(panel(page).locator('[data-hwp-page] script, [data-hwp-page] foreignObject')).toHaveCount(0)
    expect(state.unexpected).toEqual([])
  })

  test('Word and PowerPoint say they read only on a phone @phone', async ({ page }) => {
    test.skip((page.viewportSize()?.width ?? 0) > 640, 'phone width only')
    await fixture(page, 'docx')
    await expect(wordLine(page)).toBeVisible({ timeout: 30_000 })
    await expect(panel(page).getByTestId('office-read-only')).toBeVisible()
  })

  test('narrowed to a phone over unsaved edits, 저장 stays until they are saved', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'hwp')
    await hwpOpened(page)
    await hwpEdit(page, '넓은 화면 편집')
    await expect(header(page).getByText('저장 안 됨')).toBeVisible()
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(panel(page).getByTestId('office-read-only'))
      .toHaveText('이 폭에서는 읽기만 합니다. 저장하지 않은 편집을 먼저 저장하거나 버리세요.')
    await header(page).getByRole('button', { name: '저장' }).click()
    await expect(header(page).getByText('v2', { exact: true })).toBeVisible()
    await expect(header(page).getByRole('button', { name: '저장' })).toHaveCount(0)
    await expect(panel(page).locator('[data-hwp-page="1"]')).toBeVisible()
    expect(state.saves).toHaveLength(1)
  })

  test('a first load that failed is offered again', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'hwp', { failLoads: [1] })
    const notice = panel(page).getByRole('status')
    await expect(notice).toContainText('문서를 불러오지 못했습니다.')
    await notice.getByRole('button', { name: '다시 시도' }).click()
    await hwpOpened(page)
    await expect(notice).toBeHidden()
    expect(state.loads).toHaveLength(2)
  })

  test('an HWP editor that did not start is started again on request', async ({ page }) => {
    desktopOnly(page)
    test.setTimeout(150_000)
    let starts = 0
    // the first start finds no editor at /rhwp/ (it never answers); the retry finds it
    await page.route('**/rhwp/**', (route) => {
      starts += 1
      return route.fulfill({
        contentType: 'text/html', body: starts === 1 ? '<!doctype html><p>nothing here</p>' : studio,
      })
    })
    await officeFixture(page, { kind: 'hwp', file: hwpx, agentFile: hwpx, title: '직원 연수 참석 요청' })
    const notice = panel(page).getByRole('status')
    await expect(notice).toContainText('편집기를 띄우지 못했습니다', { timeout: 90_000 })
    await notice.getByRole('button', { name: '다시 시도' }).click()
    await hwpOpened(page)
    await expect(notice).toBeHidden()
  })
})

test.describe('sharing, reopening and the artifacts page', () => {
  test('an office document is shared from its panel', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'hwp')
    await hwpOpened(page)
    await header(page).getByRole('button', { name: '이 아티팩트만 공유' }).click()
    const dialog = page.getByRole('dialog', { name: '이 아티팩트만 공유' })
    await dialog.getByRole('button', { name: '링크 만들기' }).click()
    await expect.poll(() => state.shares).toEqual([{ artifactId, scope: expect.any(String) }])
  })

  test('the shared page draws the document read only, with its download', async ({ page }) => {
    const state = await fixture(page, 'hwp', { shared: { token: 'tok-hwp' } })
    const view = page.locator('[data-shared-kind="hwp"]')
    await expect(view.locator('[data-hwp-page="1"] svg')).toContainText(`document.hwpx 1쪽 ${hwpx.length}`)
    await expect(view.getByRole('button', { name: '내려받기' })).toBeEnabled()
    const download = page.waitForEvent('download')
    await view.getByRole('button', { name: '내려받기' }).click()
    expect((await download).suggestedFilename()).toBe('직원 연수 참석 요청.hwpx')
    expect(state.loads).toEqual(['shared'])
    expect(state.unexpected).toEqual([])
  })

  test('the shared page draws a Word document with its editor', async ({ page }) => {
    await fixture(page, 'docx', { shared: { token: 'tok-docx' } })
    const view = page.locator('[data-shared-kind="docx"]')
    await expect(view.locator('[data-office-view="docx"]')).toHaveAttribute('data-preview-ready', 'true', { timeout: 30_000 })
    await expect(view.locator('.odx-content').getByText(WORD_LINE).first()).toBeVisible()
  })

  test('「Word로 열기」 reopens the copy made in this chat; a new copy only when asked', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'docx', {
      attachment: { id: 'file-1', name: '2-gongmun.docx' }, copied: true,
    })
    await page.getByRole('button', { name: 'Word로 열기' }).click()
    await expect(wordLine(page)).toBeVisible({ timeout: 30_000 })
    expect(state.created).toEqual([])
    await page.getByRole('button', { name: '새 사본으로 열기' }).click()
    await expect.poll(() => state.created).toEqual([{ sessionId, fileId: 'file-1' }])
  })

  test('the artifacts page draws an office document, filters by kind, and opens it in the editor', async ({ page }) => {
    desktopOnly(page)
    await fixture(page, 'hwp', { artifactsPage: true })
    await expect(page.getByRole('tab', { name: /한글/ })).toBeVisible()
    await page.getByRole('button', { name: '직원 연수 참석 요청 열기' }).click()
    const dialog = page.getByRole('dialog', { name: '직원 연수 참석 요청' })
    await expect(dialog.locator('[data-hwp-page="1"] svg')).toContainText(`document.hwpx 1쪽 ${hwpx.length}`)
    await dialog.getByRole('button', { name: '편집기에서 열기' }).click()
    await expect(page).toHaveURL(new RegExp(`/s/${sessionId}`))
    await hwpOpened(page)
  })

  test('a document with no chat opens in the editor beside a new one', async ({ page }) => {
    desktopOnly(page)
    const state = await fixture(page, 'hwp', { orphan: true })
    await page.getByRole('button', { name: '직원 연수 참석 요청 열기' }).click()
    const dialog = page.getByRole('dialog', { name: '직원 연수 참석 요청' })
    await expect(dialog).toContainText('이 문서의 대화가 없습니다.')
    await dialog.getByRole('button', { name: '편집기에서 열기' }).click()
    await expect.poll(() => state.attached).toEqual([{ sessionId: expect.any(String) }])
    await expect(page).toHaveURL(/\/s\/[0-9a-f]+/)
    await hwpOpened(page)
  })
})
