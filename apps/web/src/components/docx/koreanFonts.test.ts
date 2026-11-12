import { describe, expect, it } from 'vitest'
import { absoluteSrc } from './koreanFonts'

describe('absoluteSrc', () => {
  it('makes each url absolute against the sheet and keeps the formats', () => {
    const src = "url(/assets/nanum-0.woff2) format('woff2'), url(\"./files/nanum-0.woff\") format('woff')"
    expect(absoluteSrc(src, 'https://chat.example/assets/index.css')).toBe(
      "url(https://chat.example/assets/nanum-0.woff2) format('woff2'), "
      + 'url("https://chat.example/assets/files/nanum-0.woff") format(\'woff\')',
    )
  })
})
