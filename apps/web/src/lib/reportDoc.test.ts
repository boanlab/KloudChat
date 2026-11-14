import { describe, expect, it } from 'vitest'
import type { JSONContent } from '@tiptap/core'
import {
  blocksToMarkdown,
  docToMarkdown,
  docToSections,
  markdownToBlocks,
  markdownToDoc,
  sectionOutline,
  sectionsToDoc,
} from '@/lib/reportDoc'
import type { ReportSection } from '@/types'

const roundTrip = (markdown: string) => blocksToMarkdown(markdownToBlocks(markdown))

const section = (id: string, heading: string, content: string, extra: Partial<ReportSection> = {}): ReportSection => ({
  id, heading, level: 1, status: 'done', content, ...extra,
})

describe('Markdown round trip', () => {
  it.each([
    '본문 **굵게**와 *기울임*, ~~취소~~, `코드`',
    '[링크](https://example.com/a) 와 https://example.com/b',
    '- 가\n- 나\n  - 다\n  - 라\n- 마',
    '1. 하나\n2. 둘\n   - 안쪽',
    '- [ ] 할 일\n- [x] 한 일',
    '> 인용 한 줄\n> 둘째 줄',
    '| 기준 | 값 |\n| --- | --- |\n| 비용 | 3억 |\n| 기간 | 6개월 |',
    '![그림 설명](data:image/png;base64,AAAA)',
    '```mermaid\nflowchart LR\n  A --> B\n```',
    '```python\nprint(1)\n```',
    '---',
    '<!-- pagebreak -->',
    '담당: [담당자], 근거 [1][2], 기한 [기한]',
    '3~5일, 100~500 Hz',
    '**「강조」**는 조사 앞에서도 닫힌다',
    '첫 줄\n둘째 줄',
    'snake_case 와 5 * 3',
  ])('%s', (markdown) => {
    expect(roundTrip(markdown)).toBe(markdown)
  })

  it('escapes text that would otherwise become structure', () => {
    const doc: JSONContent[] = [
      { type: 'paragraph', content: [{ type: 'text', text: '# 제목 아님' }] },
      { type: 'paragraph', content: [{ type: 'text', text: '1. 목록 아님' }] },
      { type: 'paragraph', content: [{ type: 'text', text: '*별표*와 [링크](아님)' }] },
    ]
    const markdown = blocksToMarkdown(doc)
    expect(markdownToBlocks(markdown)).toEqual(doc)
  })

  it('a table cell keeps its line break and pipe', () => {
    const markdown = '| 가 | 나 |\n| --- | --- |\n| 한 줄<br>두 줄 | a \\| b |'
    expect(roundTrip(markdown)).toBe(markdown)
  })
})

describe('report as one document', () => {
  const sections = [
    section('a', '개요', '본문 [1]\n\n## 소제목\n\n- 항목', { factCheck: { status: 'done', claims: [] } as never }),
    section('b', '분석', '| 가 | 나 |\n| --- | --- |\n| 1 | 2 |'),
    section('c', '결론', '끝맺음 [담당자]'),
  ]

  it('section headings carry ids; body headings sit below them', () => {
    const doc = sectionsToDoc(sections)
    const headings = (doc.content ?? []).filter((node) => node.type === 'heading')
    expect(headings.map((node) => [node.attrs?.level, node.attrs?.sectionId ?? null])).toEqual([
      [2, 'a'], [3, null], [2, 'b'], [2, 'c'],
    ])
  })

  it('an untouched document saves back exactly', () => {
    expect(docToSections(sectionsToDoc(sections), sections)).toEqual(
      sections.map((row) => ({ ...row, format: 'markdown' })),
    )
  })

  it('a stored text the editor would rewrite is kept byte for byte when untouched', () => {
    const odd = [section('x', '가', '* 별표 목록\n* 둘째')]
    expect(docToSections(sectionsToDoc(odd), odd)[0].content).toBe('* 별표 목록\n* 둘째')
  })

  it('a moved section keeps its id and fields', () => {
    const doc = sectionsToDoc(sections)
    const nodes = doc.content ?? []
    const at = nodes.findIndex((node) => node.attrs?.sectionId === 'c')
    const moved = { type: 'doc', content: [...nodes.slice(at), ...nodes.slice(0, at)] }
    const out = docToSections(moved, sections)
    expect(out.map((row) => row.id)).toEqual(['c', 'a', 'b'])
    expect(out[1].factCheck).toBeDefined()
  })

  it('an edited section drops its fact-check, others keep theirs', () => {
    const doc = sectionsToDoc(sections)
    const paragraph = (doc.content ?? []).find((node) => node.type === 'paragraph')!
    paragraph.content = [{ type: 'text', text: '고친 본문 [1]' }]
    const out = docToSections(doc, sections)
    expect(out[0].content.startsWith('고친 본문 [1]')).toBe(true)
    expect(out[0].factCheck).toBeUndefined()
    expect(out[1].content).toBe(sections[1].content)
  })

  it('deleting a heading merges the body into the section above', () => {
    const doc = sectionsToDoc(sections)
    doc.content = (doc.content ?? []).filter((node) => node.attrs?.sectionId !== 'b')
    const out = docToSections(doc, sections)
    expect(out.map((row) => row.id)).toEqual(['a', 'c'])
    expect(out[0].content).toContain('| 1 | 2 |')
  })

  it('typing a new ## heading splits a section with a new id', () => {
    const doc = sectionsToDoc(sections)
    const nodes = doc.content ?? []
    const at = nodes.findIndex((node) => node.attrs?.sectionId === 'c')
    nodes.splice(at + 1, 0, { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: '새 절' }] })
    const out = docToSections(doc, sections)
    expect(out.map((row) => row.heading)).toEqual(['개요', '분석', '결론', '새 절'])
    expect(out[3].id).not.toBe('c')
    expect(out[3].id).toBeTruthy()
    expect(out[2].content).toBe('')
  })

  it('a duplicated id becomes a new section, not two of the same', () => {
    const doc = sectionsToDoc(sections)
    doc.content = [...(doc.content ?? []), { type: 'heading', attrs: { level: 2, sectionId: 'a' }, content: [{ type: 'text', text: '복사' }] }]
    const ids = docToSections(doc, sections).map((row) => row.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('every saved section is Markdown', () => {
    const out = docToSections(sectionsToDoc([section('h', '가', '본문')]), [])
    expect(out[0].format).toBe('markdown')
  })

  it('a level-2 section stays a level-2 section', () => {
    const rows = [section('a', '상위', '본문'), section('b', '하위', '아래 ## 아님\n\n## 소제목', { level: 2 })]
    const out = docToSections(sectionsToDoc(rows), rows)
    expect(out.map((row) => [row.id, row.level])).toEqual([['a', 1], ['b', 2]])
    expect(out[1].content).toBe(rows[1].content)
  })
})

describe('Markdown 원문 view', () => {
  const sections = [section('a', '개요', '본문 **굵게**'), section('b', '분석', '- 항목\n- 둘째')]

  it('shows the same document and reads it back exactly', () => {
    const doc = sectionsToDoc(sections)
    const markdown = docToMarkdown('보고서 제목', doc)
    expect(markdown).toBe('# 보고서 제목\n\n## 개요\n\n본문 **굵게**\n\n## 분석\n\n- 항목\n- 둘째\n')
    const back = markdownToDoc(markdown, sectionOutline(doc))
    expect(back.title).toBe('보고서 제목')
    expect(back.doc).toEqual(doc)
    expect(docToMarkdown('보고서 제목', back.doc)).toBe(markdown)
  })

  it('a renamed section keeps its id by place; a new one gets a new id', () => {
    const doc = sectionsToDoc(sections)
    const edited = '# 제목\n\n## 개요 수정\n\n본문\n\n## 분석\n\n- 항목\n\n## 새 절\n\n새 본문\n'
    const back = markdownToDoc(edited, sectionOutline(doc))
    const ids = sectionOutline(back.doc).map((row) => row.id)
    expect(ids.slice(0, 2)).toEqual(['a', 'b'])
    expect(ids[2]).not.toMatch(/^[ab]$/)
  })

  it('swapped sections keep their ids by heading', () => {
    const doc = sectionsToDoc(sections)
    const back = markdownToDoc('# 제목\n\n## 분석\n\n- 항목\n\n## 개요\n\n본문\n', sectionOutline(doc))
    expect(sectionOutline(back.doc).map((row) => row.id)).toEqual(['b', 'a'])
  })

  it('a section inserted or moved in the text leaves every other section with its id and fields', () => {
    const rows = [
      section('a', '개요', '본문', { factCheck: { status: 'done', claims: [] } as never, diagrams: { k: 'png' } }),
      section('b', '분석', '- 항목'),
      section('c', '결론', '끝'),
    ]
    const doc = sectionsToDoc(rows)
    const text = docToMarkdown('제목', doc)
      .replace('## 개요', '## 새 머리말\n\n새 글\n\n## 개요')
      .replace('## 결론\n\n끝\n', '')
      .replace('## 분석', '## 결론\n\n끝\n\n## 분석')
    const back = markdownToDoc(text, sectionOutline(doc))
    const out = docToSections(back.doc, rows)
    expect(out.map((row) => row.heading)).toEqual(['새 머리말', '개요', '결론', '분석'])
    expect(out.slice(1).map((row) => row.id)).toEqual(['a', 'c', 'b'])
    expect(out[0].id).not.toMatch(/^[abc]$/)
    expect(out[1].factCheck).toBeDefined()
    expect(out[1].diagrams).toEqual({ k: 'png' })
  })
})

describe('link titles and picture names keep backslashes and quotes', () => {
  it('a title ending in a backslash does not swallow its closing quote', () => {
    const once = roundTrip('[링크](https://example.com/a "경로 C:\\\\temp\\\\ 와 \\"인용\\"")')
    expect(roundTrip(once)).toBe(once)
    expect(once).toContain('https://example.com/a')
    const picture = roundTrip('![그림 \\[1\\] C:\\\\x](data:image/png;base64,AAAA)')
    expect(roundTrip(picture)).toBe(picture)
  })
})
