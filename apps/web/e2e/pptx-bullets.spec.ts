import { readFileSync } from 'node:fs'
import JSZip from 'jszip'
import { expect, test, type Page } from '@playwright/test'
import { officeFixture } from './office-fixture'

/**
 * A bulleted line in a deck KloudChat wrote (`pptx_write`), typed into in the real editor and
 * left: the bullet stays the paragraph's own (`a:buChar` in the accent, the text hanging after
 * it), nothing typed takes the bullet's colour, an item written over two lines stays one
 * paragraph with a line break, and the saved file says so. The second deck was written the old
 * way, its bullet a 「• 」 run of its own (typing at the start of the line went into that
 * accent run, and a wrapped line started under the bullet) and its line break a raw line feed
 * in the run (read by the editor as a paragraph end), then healed by the API
 * (`pptx_edit.healed`).
 */

test.use({ serviceWorkers: 'block' })

const ACCENT = '5B5BD6'
const LONG =
  '업무 처리 시간이 부서별로 최대 3배까지 차이가 나서 결재가 늦어지고 고객 응대가 ' +
  '지연되는 일이 반복되고 있으며, 이를 줄이기 위한 절차 표준화가 필요하다'

const root = (page: Page) => page.locator('[data-pptx-panel]')
const thumbs = (page: Page) => root(page).locator('.kc-thumb')
const canvas = (page: Page) => root(page).locator('.kc-canvas')
const header = (page: Page) => page.locator('[data-panel="artifact"] header')

/** The saved slide's paragraph holding `words`: its `a:pPr` and runs ([text, colour]). */
async function paragraph(file: Buffer, words: string) {
  const zip = await JSZip.loadAsync(file)
  const xml = await zip.file('ppt/slides/slide2.xml')!.async('string')
  const found = [...xml.matchAll(/<a:p>(.*?)<\/a:p>/gs)].map((m) => m[1])
    .find((p) => [...p.matchAll(/<a:t>([^<]*)<\/a:t>/g)].map((m) => m[1]).join('').includes(words))
  expect(found, `a paragraph with ${words}`).toBeTruthy()
  const props = /<a:pPr\b[^>]*?(?:\/>|>.*?<\/a:pPr>)/s.exec(found!)?.[0] ?? ''
  const runs = [...found!.matchAll(/<a:r>(.*?)<\/a:r>/gs)].map((m) => [
    /<a:t>([^<]*)<\/a:t>/.exec(m[1])?.[1] ?? '',
    /<a:solidFill><a:srgbClr val="([0-9A-Fa-f]{6})"/.exec(m[1])?.[1]?.toUpperCase() ?? '',
  ] as const)
  return { props, runs, xml: found! }
}

for (const [name, fixture] of [
  ['written by pptx_write', 'pptx-bullets.pptx'],
  ['written the old way and healed', 'pptx-bullets-healed.pptx'],
] as const) {
  test(`a bulleted line ${name} keeps its bullet, indent and colours after it is typed into`, async ({ page }) => {
    test.skip((page.viewportSize()?.width ?? 0) <= 640, 'desktop width')
    const deck = readFileSync(new URL(`./fixtures/${fixture}`, import.meta.url))
    const state = await officeFixture(page, {
      kind: 'pptx', file: deck, agentFile: deck, title: '업무 개선 보고',
    })
    await expect(thumbs(page)).toHaveCount(3, { timeout: 30_000 })
    await thumbs(page).nth(1).click()
    const item = canvas(page).locator('[data-element-id]', { hasText: '업무 처리' }).first()
    await expect(item).toBeVisible()
    // every item's bullet stands at the same left edge, the first one too
    const lefts = await item.locator('[data-pptx-bullet-marker]').evaluateAll((marks) =>
      marks.map((mark) => Math.round(mark.getBoundingClientRect().left * 2) / 2))
    expect(lefts).toHaveLength(3)
    expect(new Set(lefts).size, String(lefts)).toBe(1)
    const box = (await item.boundingBox())!
    await page.mouse.dblclick(box.x + 60, box.y + 14)
    const editor = page.locator('[data-inline-editor]')
    await expect(editor).toBeVisible()
    // the bullet is the paragraph's, drawn apart from the text, not a character in it
    await expect(editor.locator('[data-pptx-bullet-marker]').first()).toHaveText('•')
    // typed at the very start of the line, where the old bullet run began
    await page.keyboard.press('Home')
    await page.keyboard.insertText('가나 ')
    await page.keyboard.press('Escape')
    await canvas(page).click({ position: { x: 4, y: 4 } })
    await expect(editor).toBeHidden()
    await expect(canvas(page).getByText(/가나 업무/).first()).toBeVisible()

    // the item written over two lines (a line break inside it): typed at its end, it stays
    // one bulleted paragraph, its second line not a paragraph of its own
    const second = (await canvas(page).getByText('전결 규정').first().boundingBox())!
    await page.mouse.dblclick(second.x + 8, second.y + second.height / 2)
    await expect(editor).toBeVisible()
    await page.keyboard.press('End')
    await page.keyboard.insertText(' 끝')
    await page.keyboard.press('Escape')
    await canvas(page).click({ position: { x: 4, y: 4 } })
    await expect(editor).toBeHidden()

    await header(page).getByRole('button', { name: '저장' }).click()
    await expect(header(page).getByText('v2', { exact: true })).toBeVisible()
    const { props, runs } = await paragraph(state.files.at(-1)!, '가나 업무')
    expect(runs.map(([text]) => text).join('')).toBe(`가나 ${LONG}`)
    // no bullet written as text, and no run in the bullet's accent
    expect(runs.filter(([text]) => /^\s*[•·]/.test(text))).toEqual([])
    expect(runs.filter(([, colour]) => colour === ACCENT)).toEqual([])
    expect(props).toContain('<a:buChar char="•"')
    expect(props).toMatch(new RegExp(`<a:buClr><a:srgbClr val="${ACCENT}"`, 'i'))
    expect(props).not.toContain('buNone')
    const margin = Number(/marL="(\d+)"/.exec(props)?.[1])
    const indent = Number(/indent="(-?\d+)"/.exec(props)?.[1])
    expect(margin).toBeGreaterThan(0)
    expect(indent).toBe(-margin)

    const lines = await paragraph(state.files.at(-1)!, '결재 단계 4단계')
    const texts = (xml: string) => [...xml.matchAll(/<a:t>([^<]*)<\/a:t>/g)].map((m) => m[1]).join('')
    expect(lines.xml.split(/<a:br\b/).map(texts)).toEqual(['결재 단계 4단계', '전결 규정 [개정일] 시행 끝'])
    expect(lines.xml).not.toMatch(/<a:t>[^<]*[\r\n]/)
    expect(lines.props).toContain('<a:buChar char="•"')
    expect(lines.runs.filter(([, colour]) => colour === ACCENT)).toEqual([])
    expect(state.problems).toEqual([])
  })
}
