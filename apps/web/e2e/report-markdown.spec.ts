/**
 * Round-trip tests for the report ⇄ Markdown mapping.
 *
 * No browser: saving the document editor overwrites the artifact, so this is the
 * half that has to be right before anything else matters. Playwright is used
 * only because it is the transpiler this workspace already has.
 */

import { expect, test } from '@playwright/test'
import { fromMarkdown, toMarkdown } from '../src/lib/reportMarkdown'
import type { ReportSection } from '../src/types'

const section = (id: string, heading: string, content: string): ReportSection =>
  ({ id, heading, level: 1, status: 'done', content }) as ReportSection

const REPORT = {
  title: '전이학습 기술 검토',
  sections: [
    section('s1', '서론', '배경을 설명한다.\n\n두 번째 문단이다.'),
    section('s2', '방법', '## 데이터\n수집 절차.\n\n- 항목 하나\n- 항목 둘'),
  ],
}

test('문서를 되읽으면 제목·섹션·본문이 그대로다', () => {
  const back = fromMarkdown(toMarkdown(REPORT), REPORT.sections)
  expect(back.title).toBe(REPORT.title)
  expect(back.sections.map((s) => s.heading)).toEqual(['서론', '방법'])
  expect(back.sections.map((s) => s.id)).toEqual(['s1', 's2'])
  expect(back.sections[0].content).toBe('배경을 설명한다.\n\n두 번째 문단이다.')
})

test('본문 안의 ## 는 섹션 경계가 아니라 소제목으로 남는다', () => {
  const md = toMarkdown(REPORT)
  // Two section headings in the document, not three.
  expect(md.match(/^## /gm)?.length).toBe(2)
  const back = fromMarkdown(md, REPORT.sections)
  expect(back.sections).toHaveLength(2)
  expect(back.sections[1].content).toContain('### 데이터')
})

test('두 번 왕복해도 문서가 더 이상 변하지 않는다', () => {
  const once = fromMarkdown(toMarkdown(REPORT), REPORT.sections)
  const twice = fromMarkdown(toMarkdown(once), once.sections)
  expect(toMarkdown(twice)).toBe(toMarkdown(once))
})

test('빈 줄이 살아남는다', () => {
  const back = fromMarkdown(toMarkdown(REPORT), REPORT.sections)
  expect(back.sections[0].content.split('\n\n')).toHaveLength(2)
})

test('섹션을 새로 추가하면 새 id 를 받고 기존 id 는 유지된다', () => {
  const md = `${toMarkdown(REPORT)}\n## 결론\n\n마무리한다.\n`
  const back = fromMarkdown(md, REPORT.sections)
  expect(back.sections.map((s) => s.heading)).toEqual(['서론', '방법', '결론'])
  expect(back.sections.slice(0, 2).map((s) => s.id)).toEqual(['s1', 's2'])
  expect(back.sections[2].id).not.toBe('')
})

test('섹션을 지우면 사라지고 뒤 섹션이 앞 id 를 물려받는다', () => {
  const back = fromMarkdown('# 제목\n\n## 방법\n\n수집 절차.\n', REPORT.sections)
  expect(back.sections.map((s) => s.heading)).toEqual(['방법'])
  expect(back.sections[0].id).toBe('s1')
})

test('코드 블록 안의 ## 는 섹션을 만들지 않는다', () => {
  const md = '# 제목\n\n## 서론\n\n```md\n## 이건 예시일 뿐이다\n```\n'
  const back = fromMarkdown(md, REPORT.sections)
  expect(back.sections).toHaveLength(1)
  expect(back.sections[0].content).toContain('## 이건 예시일 뿐이다')
})

test('제목 줄을 지워도 섹션은 보존된다', () => {
  const back = fromMarkdown('## 서론\n\n본문.\n', REPORT.sections)
  expect(back.sections.map((s) => s.heading)).toEqual(['서론'])
})

test('첫 제목 위에 쓴 글은 버려지지 않고 첫 섹션으로 들어간다', () => {
  const back = fromMarkdown('# 제목\n\n머리말 문장.\n\n## 서론\n\n본문.\n', REPORT.sections)
  expect(back.sections[0].content).toContain('머리말 문장.')
  expect(back.sections[0].content).toContain('본문.')
})

test('모든 소제목을 지워도 본문이 사라지지 않는다', () => {
  const back = fromMarkdown('# 제목\n\n제목 없이 쓴 본문.\n', REPORT.sections)
  expect(back.sections).toHaveLength(1)
  expect(back.sections[0].heading).toBe('서론')
  expect(back.sections[0].content).toBe('제목 없이 쓴 본문.')
})
