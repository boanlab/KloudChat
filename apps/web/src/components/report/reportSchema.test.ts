import { getSchema } from '@tiptap/core'
import { Node as PmNode } from '@tiptap/pm/model'
import { EditorState, TextSelection } from '@tiptap/pm/state'
import { undo, history } from '@tiptap/pm/history'
import { describe, expect, it } from 'vitest'
import { REPORT_EXTENSIONS, textMatches } from '@/components/report/reportSchema'
import { changedFrom, docToSections, sameSectionsAsStored, sectionsToDoc } from '@/lib/reportDoc'

const schema = getSchema(REPORT_EXTENSIONS)
const doc = PmNode.fromJSON(schema, sectionsToDoc([
  { id: 'a', heading: '비용 검토', level: 1, status: 'done', content: '총 **비용**은 3억, 비용 근거 [1]' },
  { id: 'b', heading: '결론', level: 1, status: 'done', content: '- 비용을 줄인다' },
]))

describe('find in the report editor', () => {
  it('finds across marks, in headings, in document order', () => {
    const text = (range: { from: number; to: number }) => doc.textBetween(range.from, range.to)
    const found = textMatches(doc, '총 비용은')
    expect(found.map(text)).toEqual(['총 비용은'])
    const all = textMatches(doc, '비용')
    expect(all).toHaveLength(4)
    expect(all.map((one) => one.from)).toEqual([...all.map((one) => one.from)].sort((x, y) => x - y))
    // the first is in the section heading
    expect(doc.resolve(all[0].from).parent.type.name).toBe('heading')
  })

  it('a replace-all over several sections undoes in one step', () => {
    let state = EditorState.create({ doc, plugins: [history()] })
    state = state.apply(state.tr.setSelection(TextSelection.create(state.doc, 1)))
    let tr = state.tr
    for (const match of textMatches(state.doc, '비용').reverse()) tr = tr.insertText('예산', match.from, match.to)
    state = state.apply(tr)
    expect(state.doc.textContent).not.toContain('비용')
    undo(state, (undone) => {
      state = state.apply(undone)
    })
    expect(state.doc.eq(doc)).toBe(true)
  })
})

describe('a stored report read into the editor is no edit', () => {
  const stored = [
    { id: 'a', heading: '추진 개요', level: 1 as const, status: 'done' as const, content: [
      '연간 훈련 흐름은 아래와 같다.',
      '',
      '```',
      '[시나리오 설계] --> [피싱 모의훈련] --> [결과 분석] --> [맞춤 교육]',
      '      ^                                                |',
      '      +------------------------------------------------+',
      '```',
      '',
      '**표 1. 추진 개요**',
      '',
      '| 구분 | 내용 |',
      '|---|---|',
      '| 일시 | 분기 첫째 주 수요일 14:00 (변경 가능) |',
      '| 대상 | 전 임직원 120명 |',
    ].join('\n') },
    { id: 'b', heading: '추진 흐름', level: 1 as const, status: 'done' as const, content: [
      '```mermaid',
      'flowchart LR',
      '  A[설계] --> B[훈련] --> C[분석]',
      '```',
      '',
      '| 분기 | 내용 | 비고 |',
      '| --- | --- | --- |',
      '| 2027. Q1 | 1차 훈련 |  |',
      '| 2027. Q2 | 2차 훈련 | 하반기 예산 재검토 |',
      '',
      '- 의심 메일 신고율 60% 이상',
    ].join('\n') },
  ]

  it('loads and writes back the same sections: nothing to save', () => {
    const loaded = PmNode.fromJSON(schema, sectionsToDoc(stored)).toJSON()
    const back = docToSections(loaded, stored)
    expect(sameSectionsAsStored(back, stored)).toBe(true)
  })

  it('a real edit still counts', () => {
    const edited = docToSections(PmNode.fromJSON(schema, sectionsToDoc([
      stored[0], { ...stored[1], content: stored[1].content.replace('60%', '70%') },
    ])).toJSON(), stored)
    expect(sameSectionsAsStored(edited, stored)).toBe(false)
  })

  it('a title, head table or page setup reported unchanged is no edit', () => {
    expect(changedFrom('제목', '제목')).toBeNull()
    expect(changedFrom({ numbering: 'none' }, { numbering: 'none' })).toBeNull()
    expect(changedFrom(undefined, '제목')).toBeUndefined()
    expect(changedFrom('새 제목', '제목')).toBe('새 제목')
  })
})
