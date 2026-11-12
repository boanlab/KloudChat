import { EA_RANGE, eaFamily, setFontProvider } from 'open-docx-editor'

/**
 * Korean font names a Word document asks for, drawn in a font the computer has or, failing that, in the Nanum fonts the
 * app ships: serif names (바탕, 궁서, 함초롬바탕…) in Nanum Myeongjo, sans names (맑은 고딕, 굴림, 돋움…) in Nanum Gothic
 * (`@fontsource/*`, OFL). CSS cannot alias one family to another, so each name gets its own copies of the stand-in's
 * `@font-face` rules, same files and unicode ranges, with the installed font first (`local()`): on a computer with
 * 바탕 or 맑은 고딕 the real font is used, so lines break as in Word. A face is fetched only when text needs it.
 *
 * The editor draws a run's East Asian font (w:eastAsia) under `odx-ea:<name>`, limited to Hangul, kana and Han
 * (`EA_RANGE`), so Latin text keeps the run's own font; each name gets that alias too, and `odx-ea-sans` /
 * `odx-ea-serif` stand in for a Korean font not listed here. A name's faces are added as the editor first draws it
 * (`setFontProvider`): a document asks for a few of these names, and each has some 180 faces.
 */
export const KOREAN_SERIF = [
  '바탕', '바탕체', 'Batang', 'BatangChe', '궁서', '궁서체', 'Gungsuh', 'GungsuhChe',
  '함초롬바탕', 'HCR Batang', '한컴바탕', '신명조', 'HY신명조', '휴먼명조',
  '나눔명조', 'NanumMyeongjo', 'Noto Serif KR', '본명조', 'Source Han Serif K', 'KoPub바탕', 'KoPubBatang',
  'HY견명조', '새바탕',
]
export const KOREAN_SANS = [
  '맑은 고딕', 'Malgun Gothic', '굴림', '굴림체', 'Gulim', 'GulimChe', '돋움', '돋움체', 'Dotum', 'DotumChe',
  '새굴림', 'New Gulim', '함초롬돋움', 'HCR Dotum', '한컴 고딕', '나눔고딕', 'NanumGothic', '나눔바른고딕',
  'NanumBarunGothic', 'Noto Sans KR', '본고딕', 'Source Han Sans K', 'KoPub돋움', 'KoPubDotum', 'HY견고딕',
  'HY중고딕', 'HY헤드라인M', '휴먼고딕',
]
/** Each Korean name's English one (Word writes either; an installed font answers to both). */
const TWINS: Record<string, string> = {
  '맑은 고딕': 'Malgun Gothic', 바탕: 'Batang', 바탕체: 'BatangChe', 굴림: 'Gulim', 굴림체: 'GulimChe', 돋움: 'Dotum',
  돋움체: 'DotumChe', 궁서: 'Gungsuh', 궁서체: 'GungsuhChe', 새굴림: 'New Gulim', 나눔고딕: 'NanumGothic',
  나눔명조: 'NanumMyeongjo', 나눔바른고딕: 'NanumBarunGothic', 함초롬바탕: 'HCR Batang', 함초롬돋움: 'HCR Dotum',
  본고딕: 'Source Han Sans K', 본명조: 'Source Han Serif K', KoPub바탕: 'KoPubBatang', KoPub돋움: 'KoPubDotum',
}
for (const [ko, en] of Object.entries(TWINS)) TWINS[en] = ko

const STAND_IN = { serif: 'Nanum Myeongjo', sans: 'Nanum Gothic' } as const
/** The installed fonts tried first for a Korean name the lists don't have. */
const GENERIC_LOCAL = { serif: ['Batang', '바탕', 'AppleMyungjo'], sans: ['Malgun Gothic', '맑은 고딕', 'Apple SD Gothic Neo'] }
let aliased = false

const unquote = (family: string) => family.trim().replace(/^["']|["']$/g, '')

/** `src` with each `url()` made absolute against the sheet it came from. */
export function absoluteSrc(src: string, base: string): string {
  return src.replace(/url\((["']?)([^"')]+)\1\)/g, (_, quote: string, url: string) =>
    `url(${quote}${new URL(url, base).href}${quote})`)
}

/** `local()` sources for the installed font of `names` at `weight` (its bold face by name first). */
export function localSrc(names: string[], weight: string): string {
  const bold = Number(weight) >= 600 || weight === 'bold'
  return names.flatMap((n) => {
    const q = n.replace(/["\\]/g, '')
    return bold ? [`local("${q} Bold")`, `local("${q}")`] : [`local("${q}")`]
  }).join(', ')
}

type Range = [number, number]
const parseRange = (range: string): Range[] => range.split(',').map((r) => r.trim().replace(/^U\+/i, '')).filter(Boolean)
  .map((r): Range => {
    if (r.includes('?')) return [parseInt(r.replace(/\?/g, '0'), 16), parseInt(r.replace(/\?/g, 'F'), 16)]
    const [a, b] = r.split('-')
    return [parseInt(a, 16), parseInt(b ?? a, 16)]
  })
const hex = (n: number) => n.toString(16).toUpperCase()

/** The part of a CSS unicode-range inside another, as a unicode-range ('' when they don't meet). */
export function intersectRange(a: string, b: string): string {
  const out: string[] = []
  for (const [a0, a1] of parseRange(a)) {
    for (const [b0, b1] of parseRange(b)) {
      const lo = Math.max(a0, b0)
      const hi = Math.min(a1, b1)
      if (lo <= hi) out.push(lo === hi ? `U+${hex(lo)}` : `U+${hex(lo)}-${hex(hi)}`)
    }
  }
  return out.join(', ')
}

type Face = { files: string, weight: string, descriptors: FontFaceDescriptors, eaRange: string }

/** Adds the aliases once per page (later calls do nothing): the generic ones now, each name's as it is drawn. The
 * number of faces added now. */
export function aliasKoreanFonts(doc: Document = document): number {
  if (aliased || typeof FontFace === 'undefined') return 0
  aliased = true
  const faces: Record<'serif' | 'sans', Face[]> = { serif: [], sans: [] }
  for (const sheet of Array.from(doc.styleSheets)) {
    let rules: CSSRuleList
    try {
      rules = sheet.cssRules
    } catch {
      continue // another origin's sheet
    }
    for (const rule of Array.from(rules)) {
      if (!(rule instanceof CSSFontFaceRule)) continue
      const style = rule.style
      const source = unquote(style.getPropertyValue('font-family'))
      const kind = source === STAND_IN.serif ? 'serif' : source === STAND_IN.sans ? 'sans' : null
      if (!kind) continue
      const weight = style.getPropertyValue('font-weight') || 'normal'
      const descriptors: FontFaceDescriptors = { weight, style: style.getPropertyValue('font-style') || 'normal', display: 'swap' }
      const range = style.getPropertyValue('unicode-range')
      if (range) descriptors.unicodeRange = range
      faces[kind].push({
        files: absoluteSrc(style.getPropertyValue('src'), sheet.href ?? doc.baseURI),
        weight,
        descriptors,
        // Only Hangul, kana and Han for the East Asian aliases: the files' Latin is the run's own font's job.
        eaRange: range ? intersectRange(range, EA_RANGE) : EA_RANGE,
      })
    }
  }
  let added = 0
  const add = (family: string, src: string, descriptors: FontFaceDescriptors) => {
    doc.fonts.add(new FontFace(family, src, descriptors))
    added += 1
  }
  for (const kind of ['serif', 'sans'] as const) {
    for (const f of faces[kind]) {
      if (f.eaRange) add(`odx-ea-${kind}`, `${localSrc(GENERIC_LOCAL[kind], f.weight)}, ${f.files}`, { ...f.descriptors, unicodeRange: f.eaRange })
    }
  }
  const serif = new Set(KOREAN_SERIF)
  const sans = new Set(KOREAN_SANS)
  setFontProvider((name) => {
    const kind = serif.has(name) ? 'serif' : sans.has(name) ? 'sans' : null
    if (!kind) return
    for (const f of faces[kind]) {
      const src = `${localSrc([name, TWINS[name]].filter(Boolean), f.weight)}, ${f.files}`
      add(name, src, f.descriptors)
      if (f.eaRange) add(eaFamily(name), src, { ...f.descriptors, unicodeRange: f.eaRange })
    }
  })
  return added
}
