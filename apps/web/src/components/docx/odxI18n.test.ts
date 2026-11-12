import { afterEach, describe, expect, it } from 'vitest'
// The editor's words (open-docx-editor, our patch: patches/open-docx-editor+0.7.6.patch). The package's exports don't
// reach into src/, so the module is imported by its path.
import { setLabels, setLocale, t } from '../../../node_modules/open-docx-editor/src/editor/i18n.js'

afterEach(() => setLocale(null))

describe('odx i18n', () => {
  it('translates the ribbon words into Korean and leaves English as it is', () => {
    setLocale('ko')
    expect(t('Home')).toBe('홈')
    expect(t('Font')).toBe('글꼴')
    expect(t('Bold (Ctrl+B)')).toBe('굵게 (Ctrl+B)')
    expect(t('Something the dictionary lacks')).toBe('Something the dictionary lacks')
    setLocale('en')
    expect(t('Home')).toBe('Home')
  })

  it('fills placeholders, keeps spaces round a label and splits "Label: detail"', () => {
    setLocale('ko-KR')
    expect(t('{c} × {r} table', { c: 2, r: 3 })).toBe('2 × 3 표')
    expect(t('Left ')).toBe('왼쪽 ')
    expect(t('Paste: Paste (Ctrl+V)')).toBe('붙여넣기: 붙여넣기 (Ctrl+V)')
    expect(t('Space Before: 12 pt')).toBe('단락 앞: 12 pt')
    expect(t('3 of 10, in the header')).toBe('10개 중 3번째, 머리글')
    expect(t('1 result')).toBe('결과 1개')
    setLocale('en')
    expect(t('{n} issues', { n: 4 })).toBe('4 issues')
  })

  it('takes the host\'s labels over the built-in ones', () => {
    setLocale('ko')
    setLabels({ Home: '처음' })
    expect(t('Home')).toBe('처음')
    expect(t('Insert')).toBe('삽입')
  })

  it('has a Korean entry for nearly every word the editor shows', () => {
    const sources = import.meta.glob('../../../node_modules/open-docx-editor/src/editor/{toolbar,ribbonkit,findbar,ctxmenu,pagesetup,charts,balloons,hfedit,ruler}.js', {
      query: '?raw', import: 'default', eager: true,
    }) as Record<string, string>
    expect(Object.keys(sources).length).toBeGreaterThan(5)
    const words = new Set<string>()
    const lit = String.raw`'((?:[^'\\\n]|\\.)*)'`
    const patterns = [
      new RegExp(String.raw`(?:title|'aria-label'|placeholder|label):\s*` + lit, 'g'),
      new RegExp(String.raw`\b(?:grp|item|pick|ctxItem|fileItem)\(\s*(?:'[^']*',\s*)?` + lit, 'g'),
      new RegExp(String.raw`\b(?:btn|menu|tb)\([^,()]*(?:\([^()]*\))?[^,()]*,\s*` + lit, 'g'),
      new RegExp(String.raw`\bel\('\w+',\s*\{[^{}]*\},\s*` + lit + String.raw`\)`, 'g'),
    ]
    for (const src of Object.values(sources)) {
      for (const re of patterns) for (const m of src.matchAll(re)) words.add(m[1].replace(/\\(['\\])/g, '$1'))
    }
    // Words: at least two letters, not a key name (Ctrl+X), a glyph or a CSS class.
    const shown = [...words].filter((w) => /[A-Za-z]{2}/.test(w) && !/^(Ctrl|Shift|Alt)\+|^odx-|^https?:/.test(w))
    setLocale('ko')
    const missing = shown.filter((w) => t(w) === w)
    expect(shown.length).toBeGreaterThan(150)
    expect(missing.length / shown.length, `untranslated: ${JSON.stringify(missing)}`).toBeLessThan(0.1)
  })
})
