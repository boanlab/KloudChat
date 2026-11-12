import { readFileSync } from 'node:fs'
import { deflateSync } from 'node:zlib'
import JSZip from 'jszip'
import { expect, test, type Page } from '@playwright/test'
import { artifactId, officeFixture, sessionId } from './office-fixture'

/**
 * Every action of the PowerPoint editor's chrome, each checked in the file it saves: Korean
 * typed and then made bold (the editor's own 「수정됨 수정됨」 duplicate), Korean composed by an
 * IME and bold pressed mid-syllable, the element's format from the toolbar, inserts, undo and
 * redo, slides added, copied, moved and deleted, the notes, zoom, and the saved deck opening
 * again. A manual check of the editor, as it was run when the editor was chosen:
 * `PPTX_ACTIONS=1 npx playwright test -c playwright.ci.config.ts --project 'pptx-actions/*'`.
 */

test.use({ serviceWorkers: 'block' })
test.skip(!process.env.PPTX_ACTIONS, 'manual check: PPTX_ACTIONS=1')
test.setTimeout(240_000)

const deck = readFileSync(new URL('./fixtures/pptx-business.pptx', import.meta.url))

/** A 2×2 PNG, red. */
function png(): Buffer {
  const crc = (buf: Buffer) => {
    let c = ~0
    for (const byte of buf) {
      c ^= byte
      for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1))
    }
    return ~c >>> 0
  }
  const chunk = (type: string, data: Buffer) => {
    const head = Buffer.alloc(4)
    head.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type), data])
    const tail = Buffer.alloc(4)
    tail.writeUInt32BE(crc(body))
    return Buffer.concat([head, body, tail])
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(2, 0)
  header.writeUInt32BE(2, 4)
  header[8] = 8
  header[9] = 2
  const raw = Buffer.from([0, 255, 0, 0, 255, 0, 0, 0, 255, 0, 0, 255, 0, 0])
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0)),
  ])
}

const root = (page: Page) => page.locator('[data-pptx-panel]')
const bar = (page: Page) => root(page).getByRole('toolbar', { name: '슬라이드 도구' })
const button = (page: Page, name: string) => bar(page).getByRole('button', { name, exact: true })
const thumbs = (page: Page) => root(page).locator('.kc-thumb')
const canvas = (page: Page) => root(page).locator('.kc-canvas')
const element = (page: Page, text: string) =>
  canvas(page).locator('[data-element-id]', { hasText: text }).first()
const editorText = (page: Page) =>
  page.evaluate(() => (document.querySelector('[data-inline-editor]') as HTMLElement | null)?.innerText ?? null)
const wait = (page: Page, ms = 400) => page.waitForTimeout(ms)

async function slide(page: Page, n: number) {
  await thumbs(page).nth(n - 1).click()
  await wait(page, 600)
}

async function leave(page: Page) {
  await page.keyboard.press('Escape')
  await wait(page)
  await canvas(page).click({ position: { x: 4, y: 4 } })
  await wait(page)
}

/** Each slide's XML in the saved file, in slide order. */
async function slides(file: Buffer): Promise<string[]> {
  const zip = await JSZip.loadAsync(file)
  const pres = await zip.file('ppt/presentation.xml')!.async('string')
  const rels = await zip.file('ppt/_rels/presentation.xml.rels')!.async('string')
  const ids = [...pres.matchAll(/<p:sldId [^>]*r:id="([^"]+)"/g)].map((m) => m[1])
  return Promise.all(ids.map(async (id) => {
    const target = new RegExp(`Id="${id}"[^>]*Target="([^"]+)"|Target="([^"]+)"[^>]*Id="${id}"`).exec(rels)!
    return zip.file(`ppt/${target[1] ?? target[2]}`)!.async('string')
  }))
}

/** The runs of a slide: [text, bold]. */
const runs = (xml: string) =>
  [...xml.matchAll(/<a:r>(<a:rPr[^>]*?(?:\/>|>.*?<\/a:rPr>))?<a:t>([^<]*)<\/a:t><\/a:r>/g)]
    .map((m) => [m[2], /\bb="1"/.test(m[1] ?? '')] as const)

test('every action of the chrome lands in the saved file', async ({ page }) => {
  const state = await officeFixture(page, {
    kind: 'pptx', file: deck, agentFile: deck, title: '2027년 디지털 전환 추진 계획',
  })
  await expect(thumbs(page)).toHaveCount(5, { timeout: 30_000 })
  await wait(page, 1500)
  const cdp = await page.context().newCDPSession(page)

  // at rest
  await expect(button(page, '실행 취소')).toBeDisabled()
  await expect(button(page, '굵게')).toBeDisabled()
  await expect(button(page, '텍스트 상자')).toBeEnabled()

  // Hangul typed, then the bold button: the text once, only the selection bold
  await slide(page, 2)
  let box = (await element(page, '추진 배경').boundingBox())!
  await page.mouse.dblclick(box.x + box.width * 0.6, box.y + box.height / 2)
  await wait(page, 500)
  await page.keyboard.press('End')
  await page.keyboard.type(' 수정됨')
  for (let i = 0; i < 3; i++) await page.keyboard.press('Shift+ArrowLeft')
  await expect(button(page, '굵게')).toBeEnabled()
  await button(page, '굵게').click()
  await wait(page, 600)
  const afterBold = await editorText(page)
  expect(afterBold === null || (afterBold.match(/수정됨/g) ?? []).length === 1, String(afterBold)).toBe(true)
  expect(await page.evaluate(() => window.getSelection()?.toString())).toBe('수정됨')
  await leave(page)

  // Ctrl+B on typed Hangul in the body
  box = (await element(page, '업무 처리').boundingBox())!
  await page.mouse.dblclick(box.x + 30, box.y + 12)
  await wait(page, 500)
  await page.keyboard.press('Control+End')
  await page.keyboard.type(' 추가함')
  for (let i = 0; i < 3; i++) await page.keyboard.press('Shift+ArrowLeft')
  await page.keyboard.press('Control+b')
  await wait(page, 600)
  const afterCtrlB = await editorText(page)
  expect(afterCtrlB === null || (afterCtrlB.match(/추가함/g) ?? []).length === 1, String(afterCtrlB)).toBe(true)
  await leave(page)

  // Korean composed through an IME (CDP), then bold on the composed word
  await slide(page, 1)
  box = (await element(page, '디지털 전환').boundingBox())!
  await page.mouse.dblclick(box.x + box.width * 0.5, box.y + box.height / 2)
  await wait(page, 500)
  await page.keyboard.press('End')
  await page.keyboard.type(' ')
  for (const c of ['ㅎ', '하', '한']) {
    await cdp.send('Input.imeSetComposition', { text: c, selectionStart: c.length, selectionEnd: c.length })
    await wait(page, 80)
  }
  await cdp.send('Input.insertText', { text: '한' })
  for (const c of ['ㄱ', '그', '글']) {
    await cdp.send('Input.imeSetComposition', { text: c, selectionStart: 1, selectionEnd: 1 })
    await wait(page, 80)
  }
  await cdp.send('Input.insertText', { text: '글' })
  await wait(page, 300)
  expect(await editorText(page)).toMatch(/계획 한글$/)
  await page.keyboard.press('Shift+ArrowLeft')
  await page.keyboard.press('Shift+ArrowLeft')
  await button(page, '굵게').click()
  await wait(page, 600)
  await leave(page)

  // a syllable still being composed when bold is pressed: it turns bold once it is in
  box = (await element(page, '디지털 전환').boundingBox())!
  await page.mouse.dblclick(box.x + box.width * 0.5, box.y + box.height / 2)
  await wait(page, 500)
  await page.keyboard.press('End')
  await cdp.send('Input.imeSetComposition', { text: '끝', selectionStart: 1, selectionEnd: 1 })
  await wait(page, 150)
  await button(page, '굵게').click()
  await wait(page, 300)
  await cdp.send('Input.insertText', { text: '끝' })
  await wait(page, 300)
  await leave(page)

  // the element's format from the toolbar: size, colour, italic, underline, font, alignment
  // (other than the 32 pt and centring the subtitle takes from its layout, which are not
  // written again)
  box = (await element(page, '경영기획본부').boundingBox())!
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
  await wait(page)
  await expect(button(page, '굵게')).toBeEnabled()
  await bar(page).locator('[data-testid=size] > button').click()
  await bar(page).locator('[data-testid=size] .kc-pop button', { hasText: /^20$/ }).click()
  await bar(page).locator('[data-testid=color] > button').click()
  await bar(page).locator('[data-testid=color] button[aria-label="#c00000"]').click()
  await button(page, '기울임꼴').click()
  await button(page, '밑줄').click()
  await bar(page).locator('[data-testid=font] > button').click()
  await bar(page).locator('[data-testid=font] .kc-pop button', { hasText: 'Noto Sans KR' }).click()
  await bar(page).locator('[data-testid=align] > button').click()
  await bar(page).getByRole('button', { name: '오른쪽 맞춤' }).click()
  await wait(page)
  await expect(button(page, '기울임꼴')).toHaveAttribute('aria-pressed', 'true')
  await leave(page)

  // inserts: a text box typed into, a star, a picture, a table headed in Korean
  await slide(page, 3)
  await button(page, '텍스트 상자').click()
  await wait(page, 500)
  const typed = canvas(page).locator('[data-element-id]').last()
  box = (await typed.boundingBox())!
  await page.mouse.dblclick(box.x + 10, box.y + box.height / 2)
  await wait(page)
  await page.keyboard.type('새 상자 가나다')
  await leave(page)
  await bar(page).locator('[data-testid=shape] > button').click()
  await bar(page).locator('[data-testid=shape] .kc-pop button[title="별"]').click()
  await wait(page, 500)
  await leave(page)
  await root(page).locator('[data-testid=image-input]').setInputFiles({
    name: 'red.png', mimeType: 'image/png', buffer: png(),
  })
  await wait(page, 1200)
  await expect(button(page, '굵게')).toBeDisabled()
  await leave(page)
  const before = await canvas(page).locator('[data-element-id]').count()
  await bar(page).locator('[data-testid=table] > button').click()
  await bar(page).locator('[data-testid=table] button[data-r="3"][data-c="4"]').click()
  await wait(page, 600)
  await expect(canvas(page).getByText('항목 4', { exact: true })).toBeVisible()

  // undo and redo, by button and key
  await button(page, '실행 취소').click()
  await wait(page, 500)
  await expect(canvas(page).locator('[data-element-id]')).toHaveCount(before)
  await button(page, '다시 실행').click()
  await wait(page, 500)
  await expect(canvas(page).locator('[data-element-id]')).toHaveCount(before + 1)
  await leave(page)
  await page.keyboard.press('Control+z')
  await expect(canvas(page).locator('[data-element-id]')).toHaveCount(before)
  await page.keyboard.press('Control+y')
  await expect(canvas(page).locator('[data-element-id]')).toHaveCount(before + 1)

  // slides: add, duplicate, delete, drag, move by the menu
  await root(page).locator('.kc-add').click()
  await expect(thumbs(page)).toHaveCount(6)
  await thumbs(page).nth(1).hover()
  await thumbs(page).nth(1).locator('.kc-tmore').click()
  await page.locator('.kc-ctx').getByRole('menuitem', { name: '슬라이드 복제' }).click()
  await expect(thumbs(page)).toHaveCount(7)
  await thumbs(page).nth(2).click({ button: 'right' })
  await page.locator('.kc-ctx').getByRole('menuitem', { name: '슬라이드 삭제' }).click()
  await expect(thumbs(page)).toHaveCount(6)
  // the blank one added above
  await thumbs(page).nth(3).click({ button: 'right' })
  await page.locator('.kc-ctx').getByRole('menuitem', { name: '슬라이드 삭제' }).click()
  await expect(thumbs(page)).toHaveCount(5)
  await thumbs(page).nth(4).dragTo(thumbs(page).nth(3))
  await wait(page, 700)
  await thumbs(page).nth(3).click({ button: 'right' })
  await page.locator('.kc-ctx').getByRole('menuitem', { name: '아래로 이동' }).click()
  await wait(page, 600)

  // notes, zoom
  await slide(page, 1)
  const notes = root(page).getByRole('textbox', { name: '발표자 노트' })
  await notes.click()
  await page.keyboard.press('Control+End')
  await page.keyboard.type('\n두 번째 줄 메모')
  await canvas(page).click({ position: { x: 4, y: 4 } })
  await wait(page, 800)
  await bar(page).locator('[data-testid=zoom] > button').click()
  await bar(page).locator('[data-testid=zoom] .kc-pop button', { hasText: /^50%$/ }).click()
  await expect(bar(page).locator('[data-testid=zoom] .kc-zoom')).toHaveText('50%')
  await bar(page).locator('[data-testid=zoom] > button').click()
  await bar(page).locator('[data-testid=zoom] .kc-pop button', { hasText: '맞춤' }).click()

  // Delete removes the selected element; Ctrl+Z puts it back
  await slide(page, 3)
  const count = await canvas(page).locator('[data-element-id]').count()
  box = (await canvas(page).locator('[data-element-id]').last().boundingBox())!
  await page.mouse.click(box.x + 4, box.y + 4)
  await page.keyboard.press('Delete')
  await expect(canvas(page).locator('[data-element-id]')).toHaveCount(count - 1)
  await page.keyboard.press('Control+z')
  await expect(canvas(page).locator('[data-element-id]')).toHaveCount(count)

  // save, then read the file
  const header = page.locator('[data-panel="artifact"] header')
  await header.getByRole('button', { name: '저장' }).click()
  await expect(header.getByText('v2', { exact: true })).toBeVisible()
  const saved = await slides(state.files.at(-1)!)
  expect(saved).toHaveLength(5)
  const cover = runs(saved[0])
  const coverText = cover.map(([t]) => t).join('')
  expect(coverText).toContain('2027년 디지털 전환 추진 계획 끝한글')
  expect((coverText.match(/한글/g) ?? []).length).toBe(1)
  expect(cover.filter(([, bold]) => bold).map(([t]) => t).join('')).toBe('끝한글')
  const second = runs(saved[1])
  expect(second.map(([t]) => t).join('|')).toContain('추진 배경 ')
  expect(second.filter(([t]) => t.includes('수정됨')).length).toBe(1)
  expect(second.find(([t]) => t.includes('수정됨'))?.[1]).toBe(true)
  expect(second.filter(([t]) => t.includes('추가함')).every(([, bold]) => bold)).toBe(true)
  expect((second.map(([t]) => t).join('').match(/추가함/g) ?? []).length).toBe(1)
  expect(saved[0]).toMatch(/sz="2000"/)
  expect(saved[0]).toMatch(/algn="r"/)
  expect(saved[0]).toMatch(/srgbClr val="C00000"/i)
  const third = saved[2]
  expect(third).toContain('새 상자 가나다')
  expect(third).toMatch(/prstGeom prst="star5"/)
  expect(third).toMatch(/<p:pic>/)
  expect(third).toMatch(/항목 1/)

  // the saved deck opens again as it was saved
  await page.goto(`/s/${sessionId}?artifact=${artifactId}`)
  await expect(thumbs(page)).toHaveCount(5, { timeout: 30_000 })
  await expect(canvas(page).getByText(/끝한글/).first()).toBeVisible()
  await slide(page, 3)
  await expect(canvas(page).getByText('새 상자 가나다')).toBeVisible()
  await expect(canvas(page).getByText('항목 1', { exact: true })).toBeVisible()
  expect(state.csp).toEqual([])
  expect(state.problems).toEqual([])
})
