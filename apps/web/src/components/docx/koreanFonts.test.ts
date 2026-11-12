import { describe, expect, it } from 'vitest'
import { absoluteSrc, intersectRange, localSrc } from './koreanFonts'

describe('absoluteSrc', () => {
  it('makes each url absolute against the sheet and keeps the formats', () => {
    const src = "url(/assets/nanum-0.woff2) format('woff2'), url(\"./files/nanum-0.woff\") format('woff')"
    expect(absoluteSrc(src, 'https://chat.example/assets/index.css')).toBe(
      "url(https://chat.example/assets/nanum-0.woff2) format('woff2'), "
      + 'url("https://chat.example/assets/files/nanum-0.woff") format(\'woff\')',
    )
  })
})

describe('intersectRange', () => {
  it('keeps the part of a font file\'s range that is Hangul, kana or Han', () => {
    const ea = 'U+1100-11FF, U+3000-303F, U+AC00-D7AF'
    expect(intersectRange('U+0000-00FF, U+AC00-AC40, U+3002', ea)).toBe('U+AC00-AC40, U+3002')
    expect(intersectRange('U+0000-00FF, U+0131', ea)).toBe('')
    expect(intersectRange('U+11??', ea)).toBe('U+1100-11FF')
  })
})

describe('localSrc', () => {
  it('names the installed font first, its bold face by name for a bold face', () => {
    expect(localSrc(['맑은 고딕', 'Malgun Gothic'], '400')).toBe('local("맑은 고딕"), local("Malgun Gothic")')
    expect(localSrc(['Batang'], '700')).toBe('local("Batang Bold"), local("Batang")')
  })
})
