import { describe, expect, it } from 'vitest'
import type { ReportSection, Slide } from '@/types'
import { answerUnits, deckUnits, documentUnits, reportUnits } from './readUnits'

const section = (id: string, heading: string, content: string, status: ReportSection['status'] = 'done'): ReportSection => ({
  id, heading, level: 1, status, content,
})

describe('what 읽어 주기 reads', () => {
  it('reads a chat answer as it is written', () => {
    expect(answerUnits('안녕하세요.')).toEqual([{ text: '안녕하세요.' }])
  })

  it('reads a report title first, then each finished section under its heading', () => {
    const units = reportUnits('지원 사업 보고서', [
      section('a', '개요', '올해 사업을 정리합니다.'),
      section('b', '예산', '작성 중', 'streaming'),
      section('c', '일정', '10월에 신청합니다.'),
    ])
    expect(units.map((u) => u.heading)).toEqual(['지원 사업 보고서', '개요', '일정'])
    expect(units.map((u) => u.at)).toEqual([undefined, 'a', 'c'])
  })

  it('leaves out drawn figures and page-break comments', () => {
    const [, only] = reportUnits('제목', [
      section('a', '구조', ['앞 글입니다.', '', '```mermaid', 'graph TD; A-->B', '```', '', '<!-- pagebreak -->', '', '뒤 글입니다.'].join('\n')),
    ])
    expect(only.text).toContain('앞 글입니다.')
    expect(only.text).toContain('뒤 글입니다.')
    expect(only.text).not.toContain('graph TD')
    expect(only.text).not.toContain('pagebreak')
  })

  it('keeps an ordinary code block (it is told about, not read)', () => {
    const [, only] = reportUnits('제목', [section('a', '코드', '```python\nprint(1)\n```')])
    expect(only.text).toContain('```python')
  })

  it('reads a slide’s presenter notes, and its own lines when it has none', () => {
    const slides = [
      { id: 's1', layout: 'bullets', title: '배경', bullets: ['문제', '목표'], notes: '발표 때는 이렇게 말합니다.' },
      { id: 's2', layout: 'bullets', title: '계획', bullets: ['1단계', '2단계'], body: '일정은 아래와 같습니다.' },
    ] as Slide[]
    const units = deckUnits(slides, (n) => `${n}번 슬라이드`)
    expect(units[0]).toMatchObject({ label: '1번 슬라이드', at: '0', heading: '배경', text: '발표 때는 이렇게 말합니다.' })
    expect(units[1].text).toBe('- 1단계\n- 2단계\n\n일정은 아래와 같습니다.')
  })

  it('turns a document’s text lines into paragraphs', () => {
    expect(documentUnits('계획서', '첫 줄\n\n\n둘째 줄\n')).toEqual([
      { label: '계획서', heading: '계획서', text: '첫 줄\n\n둘째 줄' },
    ])
    expect(documentUnits('빈 문서', '  \n ')).toEqual([])
  })
})
