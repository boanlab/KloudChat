import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { artifactId, officeFixture, sessionId, type OfficeOptions } from './office-fixture'

/**
 * HWP, Word and PowerPoint files in the file box: the preview draws them as their editor does
 * (read only), and 「편집」 opens a copy in that editor beside a new chat, the file itself never
 * written; a file opened before offers its document again rather than another copy.
 */

test.use({ serviceWorkers: 'block' })

const hwpx = readFileSync(new URL('./fixtures/hwp-official.hwpx', import.meta.url))
const docx = readFileSync(new URL('./fixtures/word-official.docx', import.meta.url))
const pptx = readFileSync(new URL('./fixtures/pptx-plan.pptx', import.meta.url))
const studio = readFileSync(new URL('./fixtures/fake-rhwp.html', import.meta.url), 'utf8')
const boxId = 'f1'.padEnd(32, '0')

const KINDS = {
  hwp: { file: hwpx, name: '직원 연수 공문.hwpx' },
  docx: { file: docx, name: '직원 연수 공문.docx' },
  pptx: { file: pptx, name: '2027 사업 계획.pptx' },
} as const

async function fixture(page: Page, kind: keyof typeof KINDS, options: Partial<OfficeOptions> = {}) {
  // the HWP editor's stand-in (`fake-rhwp.html`), which draws a page as SVG
  await page.route('**/rhwp/**', (route) => route.fulfill({ contentType: 'text/html', body: studio }))
  const { file, name } = KINDS[kind]
  return officeFixture(page, {
    kind, file, agentFile: file, title: '직원 연수 참석 요청',
    box: { id: boxId, name }, ...options,
  })
}

const dialogOf = (page: Page, kind: keyof typeof KINDS) =>
  page.getByRole('dialog', { name: KINDS[kind].name })
const preview = (page: Page, kind: keyof typeof KINDS) =>
  dialogOf(page, kind).getByTestId('file-preview')

async function openPreview(page: Page, kind: keyof typeof KINDS) {
  await page.getByRole('button', { name: KINDS[kind].name, exact: true }).click()
  await expect(preview(page, kind)).toHaveAttribute('data-preview-ready', 'true', { timeout: 30_000 })
}

/** What each editor draws of the fixture file, read only. */
const drawn = {
  hwp: async (page: Page) => {
    const sheet = preview(page, 'hwp').locator('[data-hwp-page="1"] svg')
    await expect(sheet).toContainText(`document.hwpx 1쪽 ${hwpx.length}`)
    // what the drawing carried that could run is gone, and nothing ran
    await expect(sheet.locator('script, foreignObject')).toHaveCount(0)
    expect(await sheet.getAttribute('onload')).toBeNull()
    expect(await sheet.locator('a').getAttribute('href')).toBeNull()
    expect(await page.evaluate(() => (window as unknown as { pwned?: number }).pwned)).toBeUndefined()
    // the editor that drew it is out of sight
    await expect(preview(page, 'hwp').locator('iframe')).toHaveAttribute('aria-hidden', 'true')
  },
  docx: async (page: Page) => {
    const content = preview(page, 'docx').locator('.odx-content')
    await expect(content.getByText('붙임  1. 연수 일정표 1부.').first()).toBeVisible()
    await expect(content.first()).toHaveAttribute('contenteditable', 'false')
    await expect(preview(page, 'docx').locator('.odx-toolbar')).toBeHidden()
  },
  pptx: async (page: Page) => {
    await expect(preview(page, 'pptx').locator('.kc-thumb')).toHaveCount(5, { timeout: 30_000 })
    await expect(preview(page, 'pptx').locator('.kc-canvas').getByText('2027년 사업 계획 보고').first())
      .toBeVisible()
    await expect(preview(page, 'pptx').getByRole('toolbar')).toHaveCount(0)
  },
}

const panelOf = (page: Page, kind: keyof typeof KINDS) => page.locator(`[data-${kind}-panel]`)

for (const kind of ['hwp', 'docx', 'pptx'] as const) {
  test(`a ${kind} file previews as its editor draws it, and 편집 opens a copy beside a new chat`, async ({ page }) => {
    test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
    const state = await fixture(page, kind)
    await openPreview(page, kind)
    await drawn[kind](page)
    const dialog = dialogOf(page, kind)
    await expect(dialog.getByTestId('file-edit-note')).toContainText('이 파일은 그대로 남습니다')
    await expect(dialog.getByRole('button', { name: '이어서 편집' })).toHaveCount(0)

    await dialog.getByRole('button', { name: '편집', exact: true }).click()
    // `?artifact=` opens the copy and is then cleared from the address
    await expect(page).toHaveURL(/\/s\/[0-9a-z_-]+/)
    await expect(panelOf(page, kind)).toHaveAttribute(`data-${kind}-panel`, artifactId)
    await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
    expect(state.created).toEqual([{ sessionId: expect.any(String), fileId: boxId }])
    // the copy is titled from the file; the file itself was never written
    await expect(panelOf(page, kind).locator('header')).toContainText(KINDS[kind].name.replace(/\.[^.]+$/, ''))
    expect(state.fileWrites).toEqual([])
    expect(state.saves).toEqual([])
    expect(state.unexpected).toEqual([])
    expect(state.problems).toEqual([])
    expect(state.csp).toEqual([])
  })
}

test('a file opened before offers its document again, and a new copy only when asked', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page, 'docx', { copied: true })
  await openPreview(page, 'docx')
  const dialog = dialogOf(page, 'docx')
  await expect(dialog.getByTestId('file-edit-note')).toContainText('이 파일로 만든 문서가 있습니다: 직원 연수 참석 요청 · v1')
  await expect(dialog.getByRole('button', { name: '편집', exact: true })).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: '새 사본으로 편집' })).toBeEnabled()
  await dialog.getByRole('button', { name: '이어서 편집' }).click()
  await expect(page).toHaveURL(new RegExp(`/s/${sessionId}`))
  await expect(panelOf(page, 'docx')).toHaveAttribute('data-docx-panel', artifactId)
  expect(state.created).toEqual([])
  expect(state.fileWrites).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('a file its editor cannot draw shows its text, saying so', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
  const state = await fixture(page, 'docx', {
    box: { id: boxId, name: KINDS.docx.name, boxFile: Buffer.from('not a word file'), text: '1. 연수 일정' },
  })
  await page.getByRole('button', { name: KINDS.docx.name, exact: true }).click()
  const dialog = dialogOf(page, 'docx')
  await expect(dialog.getByTestId('file-preview-note')).toHaveText('이 파일은 모양대로 그리지 못해 글자만 보여 줍니다.', { timeout: 30_000 })
  await expect(dialog.getByTestId('file-preview')).toContainText('1. 연수 일정')
  expect(state.unexpected).toEqual([])
})

for (const kind of ['hwp', 'docx', 'pptx'] as const) {
  test(`on a phone the ${kind} preview fits the screen and only reads @phone`, async ({ page }) => {
    test.skip((page.viewportSize()?.width ?? 0) > 640, 'phone width only')
    const state = await fixture(page, kind)
    await openPreview(page, kind)
    await drawn[kind](page)
    const dialog = dialogOf(page, kind)
    await expect(dialog.getByTestId('file-edit-note')).toContainText('휴대폰에서는 읽기만 합니다')
    const box = (await preview(page, kind).boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width)
    await expect(dialog.getByRole('button', { name: '편집', exact: true })).toBeVisible()
    expect(state.fileWrites).toEqual([])
    expect(state.problems).toEqual([])
    expect(state.csp).toEqual([])
  })
}
