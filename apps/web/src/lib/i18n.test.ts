import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'
import { hasEnglish, hasEnglishIn } from './i18n'

// Every literal Korean string handed to a translator must have English, or the English UI shows Korean.
const SRC = join(__dirname, '..')
const HANGUL = /[가-힣ㄱ-ㆎ]/

function sourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) out.push(...sourceFiles(path))
    else if (/\.tsx?$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name) && !name.endsWith('.d.ts')) out.push(path)
  }
  return out
}

// A quoted literal: '…', "…" or `…` with backslash escapes.
const LITERAL = String.raw`('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|\x60(?:[^\x60\\]|\\.)*\x60)`
// A ternary's condition: anything but a call, `?` or statement end, quoted literals included.
const CONDITION = String.raw`(?:[^()'"\x60;?]|'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")*?`

function unquote(lit: string): string | null {
  const body = lit.slice(1, -1)
  if (lit[0] === '`' && body.includes('${')) return null
  return body.replace(/\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)/gs, (_, e: string) => {
    if (e[0] === 'u') return String.fromCodePoint(parseInt(e.replace(/[u{}]/g, ''), 16))
    if (e[0] === 'x' && e.length === 3) return String.fromCharCode(parseInt(e.slice(1), 16))
    return ({ n: '\n', t: '\t', r: '\r' } as Record<string, string>)[e] ?? e
  })
}

type Call = { file: string; line: number; context: string | null; text: string }

/** Literal Korean arguments of `t(…)`, `tr(…)`, `useTIn` translators, `translate(lang, …)` and `translateIn(lang, ctx, …)`. */
function translatorCalls(file: string, src = readFileSync(file, 'utf8')): Call[] {
  const calls: Call[] = []
  const lineOf = (i: number) => src.slice(0, i).split('\n').length
  const push = (i: number, context: string | null, lit: string) => {
    const text = unquote(lit)
    if (text !== null && HANGUL.test(text)) calls.push({ file: relative(SRC, file), line: lineOf(i), context, text })
  }
  // Translators bound to a context in this file: `const tHub = useTIn('hub')`.
  const contexts = new Map<string, string>()
  for (const m of src.matchAll(/\b(\w+)\s*=\s*useTIn\(\s*['"]([^'"]+)['"]\s*\)/g)) contexts.set(m[1], m[2])
  const names = ['t', 'tr', ...contexts.keys()].join('|')
  // A standalone identifier (not `.t(`, `split(`, `xt(`), optional chaining allowed.
  const direct = new RegExp(String.raw`(?<![\w$.])(${names})\s*(?:\?\.)?\(\s*${LITERAL}`, 'g')
  for (const m of src.matchAll(direct)) push(m.index!, contexts.get(m[1]) ?? null, m[2])
  // `t(cond ? '…' : '…')`: both branches.
  const ternary = new RegExp(String.raw`(?<![\w$.])(${names})\(\s*${CONDITION}\?\s*${LITERAL}\s*:\s*${LITERAL}\s*,?\s*\)`, 'g')
  for (const m of src.matchAll(ternary)) for (const lit of [m[2], m[3]]) push(m.index!, contexts.get(m[1]) ?? null, lit)
  const tr = new RegExp(String.raw`(?<![\w$.])translate\(\s*[^,()]+,\s*${LITERAL}`, 'g')
  for (const m of src.matchAll(tr)) push(m.index!, null, m[1])
  const trIn = new RegExp(String.raw`(?<![\w$.])translateIn\(\s*[^,()]+,\s*['"]([^'"]+)['"]\s*,\s*${LITERAL}`, 'g')
  for (const m of src.matchAll(trIn)) push(m.index!, m[1], m[2])
  return calls
}

describe('English dictionary', () => {
  it('finds translator calls but not look-alikes', () => {
    const sample = [
      "const tHub = useTIn('hub')",
      "x.split('가'); obj.t('나'); next('다'); t(`라 ${n}`)",
      "t('마'); tr(\"바\"); t?.('사'); tHub('탐색'); t(\n  mode === 'x'\n    ? '아'\n    : '자',\n)",
      "translate(lang, '차'); translateIn(lang, 'hub', '개요'); t('그\\'s')",
    ].join('\n')
    const found = translatorCalls(join(SRC, 'sample.ts'), sample).map((c) => (c.context ? `${c.context}|${c.text}` : c.text))
    expect(found.sort()).toEqual(['hub|개요', 'hub|탐색', "그's", '마', '바', '사', '아', '자', '차'].sort())
    expect(unquote('`a ${b} 가`')).toBeNull()
  })

  it('has English for every literal Korean string passed to a translator', () => {
    const missing = sourceFiles(SRC)
      .flatMap((file) => translatorCalls(file))
      .filter((c) => (c.context ? !hasEnglishIn(c.context, c.text) : !hasEnglish(c.text)))
      .map((c) => `${c.file}:${c.line}${c.context ? ` [${c.context}]` : ''}  ${JSON.stringify(c.text)}`)
    expect([...new Set(missing)]).toEqual([])
  })
})
