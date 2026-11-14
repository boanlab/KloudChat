import { describe, expect, it } from 'vitest'
import { citationHref, linkCitations, materialSources } from './shelfCitations'

const ANSWER = [
  '벡터 시계로는 동시성을 판단할 수 있습니다 [2]. 램포트 시계는 순서만 정합니다 [1, 2].',
  '',
  '```',
  'code [1]',
  '```',
  '',
  '### 출처',
  '- [1] 03강 논리 시계.pdf · 3쪽',
  '- [2] 래프트 강의.mp4',
  '- [3] [뉴스 · example.com](https://example.com/a) · 확인 2026-11-13',
].join('\n')

describe('project-source citations', () => {
  it('reads the material items of the source list, not web ones', () => {
    const sources = materialSources(ANSWER)
    expect([...sources.keys()]).toEqual([1, 2])
    expect(sources.get(1)).toEqual({ name: '03강 논리 시계.pdf', page: 3 })
    expect(sources.get(2)?.page).toBeNull()
  })

  it('links inline citations with the claim each closes, outside code and the list', () => {
    const { content, claims } = linkCitations(ANSWER)
    expect(content).toContain('판단할 수 있습니다 [[2]](#cite-2-0).')
    expect(content).toContain('[[1]](#cite-1-1)[[2]](#cite-2-2)')
    expect(content).toContain('code [1]')
    expect(content).toContain('- [1] 03강 논리 시계.pdf · 3쪽')
    expect(claims[0]).toBe('벡터 시계로는 동시성을 판단할 수 있습니다')
    expect(claims[1]).toBe('램포트 시계는 순서만 정합니다')
    expect(citationHref('#cite-2-0')).toEqual([2, 0])
    expect(citationHref('https://x')).toBeNull()
  })

  it('leaves an answer with no material list alone', () => {
    const plain = '답 [1].\n\n### 출처\n- [1] [제목](https://example.com)'
    expect(linkCitations(plain).content).toBe(plain)
  })
})
