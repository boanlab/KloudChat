import { getSchema } from '@tiptap/core'
import { Node as PmNode } from '@tiptap/pm/model'
import { EditorState, TextSelection } from '@tiptap/pm/state'
import { undo, history } from '@tiptap/pm/history'
import { describe, expect, it } from 'vitest'
import { REPORT_EXTENSIONS, textMatches } from '@/components/report/reportSchema'
import { sectionsToDoc } from '@/lib/reportDoc'

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
