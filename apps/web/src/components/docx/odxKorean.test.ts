import { DOMParser, XMLSerializer } from '@xmldom/xmldom'
import { strFromU8, strToU8, unzipSync, zipSync } from 'fflate'
import { blankDocxBytes, commands, DocxSession, mergeRFonts, setXmlImpl } from 'open-docx-editor'
import { EditorState, TextSelection, type Transaction } from 'prosemirror-state'
import type { Node } from 'prosemirror-model'
import { beforeAll, describe, expect, it } from 'vitest'
import propsJs from '../../../node_modules/open-docx-editor/src/ooxml/props.js?raw'
import writerJs from '../../../node_modules/open-docx-editor/src/ooxml/writer.js?raw'
import i18nJs from '../../../node_modules/open-docx-editor/src/editor/i18n.js?raw'

// Korean in the Word editor: open-docx-editor with our patch (patches/open-docx-editor+0.7.6.patch), read and written
// as XML. The patch must be applied: `npm ci` runs patch-package (postinstall); these fail loudly when it wasn't.

const W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
const THEME = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="t"><a:themeElements><a:fontScheme name="f">
<a:majorFont><a:latin typeface="Cambria"/><a:ea typeface=""/><a:cs typeface=""/><a:font script="Hang" typeface="바탕"/></a:majorFont>
<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/><a:font script="Jpan" typeface="ＭＳ 明朝"/><a:font script="Hang" typeface="맑은 고딕"/></a:minorFont>
</a:fontScheme></a:themeElements></a:theme>`

/** A .docx of the blank template with this body, document defaults' run properties and (optionally) a theme. */
function docx(body: string, { rPrDefault = '', theme = false } = {}): Uint8Array {
  const files = unzipSync(blankDocxBytes())
  const doc = strFromU8(files['word/document.xml'])
  files['word/document.xml'] = strToU8(doc.replace('<w:body><w:p/>', `<w:body>${body}`))
  if (rPrDefault) {
    const styles = strFromU8(files['word/styles.xml'])
    files['word/styles.xml'] = strToU8(styles.replace(/<w:rPrDefault><w:rPr>[\s\S]*?<\/w:rPr>/, `<w:rPrDefault><w:rPr>${rPrDefault}</w:rPr>`))
  }
  if (theme) {
    files['word/theme/theme1.xml'] = strToU8(THEME)
    const rels = strFromU8(files['word/_rels/document.xml.rels'])
    files['word/_rels/document.xml.rels'] = strToU8(rels.replace('</Relationships>',
      '<Relationship Id="rIdTheme" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/></Relationships>'))
    const types = strFromU8(files['[Content_Types].xml'])
    files['[Content_Types].xml'] = strToU8(types.replace('</Types>',
      '<Override PartName="/word/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>'))
  }
  return zipSync(files)
}
const run = (rPr: string, text: string) => `<w:r>${rPr ? `<w:rPr>${rPr}</w:rPr>` : ''}<w:t xml:space="preserve">${text}</w:t></w:r>`
const para = (...runs: string[]) => `<w:p>${runs.join('')}</w:p>`
const KO = '대한민국 헌법 제1조 국민으로부터 나온다'

/** document.xml of the session's document saved as it stands (or as `doc`). */
const saved = (s: DocxSession, doc?: Node) => strFromU8(unzipSync(s.save(doc))['word/document.xml'])
/** Picks `font` in Home > Font over the paragraph that starts with `needle`; the document after it. */
function pick(s: DocxSession, needle: string, font: string): Node {
  let state = EditorState.create({ doc: s.doc as Node })
  let from = 0
  let to = 0
  state.doc.descendants((n, pos) => {
    if (!from && n.isTextblock && n.textContent.startsWith(needle)) { from = pos + 1; to = pos + 1 + n.content.size }
    return !from
  })
  state = state.apply(state.tr.setSelection(TextSelection.create(state.doc, from, to)))
  const setFont = commands.setFont as (name: string | null) => (st: EditorState, d: (tr: Transaction) => void) => boolean
  setFont(font)(state, (tr) => { state = state.apply(tr) })
  return state.doc
}
const rFonts = (xml: string, needle: string) => {
  const p = xml.split('</w:p>').find((x) => x.includes(needle)) ?? ''
  return p.match(/<w:rFonts\b[^>]*\/>/g) ?? []
}

beforeAll(() => setXmlImpl({ DOMParser, XMLSerializer }))

describe('the open-docx-editor patch', () => {
  it('is applied (sentinel in the patched source)', () => {
    const files = { 'ooxml/props.js': propsJs, 'ooxml/writer.js': writerJs, 'editor/i18n.js': i18nJs }
    for (const [file, src] of Object.entries(files)) {
      expect(src, `${file} is not patched: run npm ci (patch-package applies patches/)`).toContain('kloudchat-odx-patch')
    }
    expect(typeof commands.setFont).toBe('function')
  })
})

describe('East Asian fonts when drawing', () => {
  it('draws Hangul in the run\'s w:eastAsia font and Latin in its ascii font', () => {
    const s = new DocxSession(docx(para(run('<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="바탕"/>', KO))))
    const json = JSON.stringify((s.doc as Node).toJSON())
    expect(json).toContain('w:eastAsia=\\"바탕\\"')
    // the style rules: the document defaults' East Asian font (theme) and the run's own, as the --odx-fea list
    const t = new DocxSession(docx(para(run('', KO)), {
      theme: true,
      rPrDefault: '<w:rFonts w:asciiTheme="minorHAnsi" w:eastAsiaTheme="minorEastAsia" w:hAnsiTheme="minorHAnsi"/><w:lang w:val="en-US" w:eastAsia="ko-KR"/>',
    }))
    const css = t.css('.d')
    expect(css).toContain('--odx-fea:"odx-ea:맑은 고딕", "odx-ea-sans",') // a:ea empty: the Hang script's font (ko-KR)
    expect(css).toContain('--odx-fea-major:"odx-ea:바탕", "odx-ea-serif",')
    expect(css).toContain('font-family:var(--odx-fea,) var(--odx-fa)')
  })

  it('takes Korean line heights and word breaking only in a Korean document', () => {
    const ko = new DocxSession(docx(para(run('', KO)), { rPrDefault: '<w:rFonts w:ascii="맑은 고딕" w:hAnsi="맑은 고딕" w:eastAsia="맑은 고딕"/><w:lang w:val="ko-KR" w:eastAsia="ko-KR"/>' }))
    expect(ko.korean).toBe(true)
    expect(ko.css('.d')).toContain('--odx-lh:1.3301') // 맑은 고딕's line (its hhea ascent + descent)
    const en = new DocxSession(docx(para(run('', 'An English document, with a word of Korean: 국민.')), { rPrDefault: '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="맑은 고딕"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/>' }))
    expect(en.korean).toBe(false)
    expect(en.css('.d')).toContain('--odx-lh:1.2207') // Calibri's own
  })
})

describe('Home > Font on Korean text', () => {
  it('a Korean font sets eastAsia and the hint; one w:rFonts', () => {
    const s = new DocxSession(docx(para(run('<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>', `바꿀 ${KO}`))))
    const xml = saved(s, pick(s, '바꿀', '나눔고딕'))
    expect(rFonts(xml, '바꿀')).toEqual(['<w:rFonts w:hint="eastAsia" w:ascii="나눔고딕" w:hAnsi="나눔고딕" w:eastAsia="나눔고딕" w:cs="Arial"/>'])
  })

  it('a Latin font keeps the run\'s East Asian font', () => {
    const s = new DocxSession(docx(para(run('<w:rFonts w:ascii="바탕" w:hAnsi="바탕" w:eastAsia="바탕" w:hint="eastAsia"/>', `바꿀 ${KO}`))))
    const xml = saved(s, pick(s, '바꿀', 'Arial'))
    expect(rFonts(xml, '바꿀')).toEqual(['<w:rFonts w:hint="eastAsia" w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="바탕" w:cs="Arial"/>'])
  })

  it('folds an East-Asian-only, hint-only or theme w:rFonts into one, the theme reference dropped for the font chosen', () => {
    const s = new DocxSession(docx([
      para(run('<w:rFonts w:eastAsia="나눔명조" w:hint="eastAsia"/>', '동아시아만 가나다')),
      para(run('<w:rFonts w:hint="eastAsia"/>', '힌트만 가나다')),
      para(run('<w:rFonts w:asciiTheme="minorHAnsi" w:eastAsiaTheme="minorEastAsia" w:hAnsiTheme="minorHAnsi"/>', '테마 가나다')),
    ].join('')))
    let doc = pick(s, '동아시아만', 'Arial')
    s.doc = doc
    doc = pick(s, '힌트만', 'Arial')
    s.doc = doc
    doc = pick(s, '테마', 'Georgia')
    const xml = saved(s, doc)
    expect(rFonts(xml, '동아시아만')).toEqual(['<w:rFonts w:hint="eastAsia" w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="나눔명조" w:cs="Arial"/>'])
    expect(rFonts(xml, '힌트만')).toEqual(['<w:rFonts w:hint="eastAsia" w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'])
    expect(rFonts(xml, '테마')).toEqual(['<w:rFonts w:ascii="Georgia" w:hAnsi="Georgia" w:cs="Georgia" w:eastAsiaTheme="minorEastAsia"/>'])
  })

  it('writes a font set over a raw w:rFonts by other means (paste) as one element too', () => {
    expect(mergeRFonts('<w:rFonts w:eastAsia="바탕"/>', 'Arial')).toBe('<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="바탕" w:cs="Arial"/>')
    expect(mergeRFonts(null, '굴림')).toBe('<w:rFonts w:hint="eastAsia" w:ascii="굴림" w:hAnsi="굴림" w:eastAsia="굴림"/>')
  })

  it('leaves the runs it was not asked to change byte for byte', () => {
    const body = [
      para(run('<w:rFonts w:ascii="맑은 고딕" w:eastAsia="맑은 고딕" w:hAnsi="맑은 고딕" w:hint="eastAsia"/><w:lang w:val="ko-KR" w:eastAsia="ko-KR"/>', KO)),
      para(run('<w:rFonts w:asciiTheme="minorHAnsi" w:eastAsiaTheme="minorEastAsia"/>', '각 NFD 한글')),
      para(run('', '바꿀 문단')),
    ].join('')
    const s = new DocxSession(docx(body))
    const before = saved(s)
    const after = saved(s, pick(s, '바꿀', '바탕'))
    const untouched = (x: string) => x.split('</w:p>').slice(0, 2).join('</w:p>')
    expect(untouched(after)).toBe(untouched(before))
    expect(new XMLSerializer().serializeToString(new DOMParser().parseFromString(after, 'application/xml'))).toContain(W)
  })
})
