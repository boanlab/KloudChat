/**
 * Korean serif names a Word document asks for (바탕, 궁서, 함초롬바탕…), drawn in the Nanum
 * Myeongjo the app already ships (`index.css`). The Word editor sets a run in the font its
 * file names and, for a name it does not know, falls back to sans-serif; CSS cannot alias one
 * family to another, so each of these names gets its own copies of Nanum Myeongjo's
 * `@font-face` rules, same files and unicode ranges. A face is fetched only when text needs it.
 * Korean sans names (맑은 고딕, 돋움, 굴림) are left to the computer's sans-serif font.
 */
export const KOREAN_SERIF = [
  '바탕', '바탕체', 'Batang', 'BatangChe', '궁서', '궁서체', 'Gungsuh', 'GungsuhChe',
  '함초롬바탕', 'HCR Batang', '한컴바탕', '신명조', 'HY신명조', '휴먼명조',
]

const SOURCE = 'Nanum Myeongjo'
let aliased = false

const unquote = (family: string) => family.trim().replace(/^["']|["']$/g, '')

/** `src` with each `url()` made absolute against the sheet it came from. */
export function absoluteSrc(src: string, base: string): string {
  return src.replace(/url\((["']?)([^"')]+)\1\)/g, (_, quote: string, url: string) =>
    `url(${quote}${new URL(url, base).href}${quote})`)
}

/** Adds the aliases once per page; later calls do nothing. */
export function aliasKoreanFonts(doc: Document = document): number {
  if (aliased || typeof FontFace === 'undefined') return 0
  aliased = true
  let added = 0
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
      if (unquote(style.getPropertyValue('font-family')) !== SOURCE) continue
      const src = absoluteSrc(style.getPropertyValue('src'), sheet.href ?? doc.baseURI)
      const descriptors: FontFaceDescriptors = {
        weight: style.getPropertyValue('font-weight') || 'normal',
        style: style.getPropertyValue('font-style') || 'normal',
        display: 'swap',
      }
      const range = style.getPropertyValue('unicode-range')
      if (range) descriptors.unicodeRange = range
      for (const family of KOREAN_SERIF) {
        doc.fonts.add(new FontFace(family, src, descriptors))
        added += 1
      }
    }
  }
  return added
}
