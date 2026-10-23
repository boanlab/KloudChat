import { describe, expect, it } from 'vitest'
import { agentHandoffSurface, handoffSurface } from '@/lib/documentRequest'

describe('handoffSurface', () => {
  it('hands an English request to create, make or build a deck to the slides screen', () => {
    expect(
      handoffSurface(
        "Create a 6-slide English presentation for a board update: 'Q3 2026 results.' Numbers: 412 installs, revenue $9.8M.",
      ),
    ).toBe('slides')
    expect(handoffSurface('Make a 5-slide deck on our hiring plan')).toBe('slides')
    expect(handoffSurface('Can you build a slide deck for the kickoff?')).toBe('slides')
    expect(handoffSurface('Please put together a presentation on Q4 targets')).toBe('slides')
    expect(handoffSurface('Create a report on our Q3 support tickets')).toBe('report')
  })

  it('hands a Korean request to produce or prepare a deck to the slides screen', () => {
    expect(handoffSurface('신제품 소개 발표 자료 제작해 줘')).toBe('slides')
    expect(handoffSurface('투자 유치용 피피티 짜줘')).toBe('slides')
    expect(handoffSurface('주간 회의 슬라이드 준비해 줘')).toBe('slides')
  })

  it('keeps questions about decks in the chat', () => {
    expect(handoffSurface('How do I make a good presentation?')).toBeNull()
    expect(handoffSurface('What makes a slide deck easy to follow?')).toBeNull()
    expect(handoffSurface('Should I create a separate deck for the board?')).toBeNull()
    expect(handoffSurface('발표 자료 만드는 방법 알려줘')).toBeNull()
    expect(handoffSurface('보고서와 기획서 차이가 뭐야? 만들어 보기 전에 알고 싶어')).toBeNull()
  })

  it('reads the question words in the instruction, not in the data pasted into it', () => {
    expect(
      handoffSurface(
        '본부장님께 올릴 재택근무 시범 운영 결과 보고서를 만들어줘. 인사팀 작성. 자료: 만족도 3.4점 → 4.1점. 납기 준수율 88% → 90%(차이 작음). 제안: 신입은 재택 제외.',
      ),
    ).toBe('report')
    expect(handoffSurface('분기 실적 보고서 작성해 줘\n- 매출 41.2억\n- 전년 대비 차이 작음\n- 개선 방법 검토 중')).toBe('report')
    expect(handoffSurface('보고서 작성 방법 알려줘. 자료: 매출 41.2억')).toBeNull()
  })

  it('takes linear time on a long run of spaces', () => {
    const started = performance.now()
    handoffSurface(`보고서 만들어줘.${' '.repeat(50000)}자료`)
    handoffSurface(`보고서 만들어줘. ${'가'.repeat(1100)}`)
    expect(performance.now() - started).toBeLessThan(500)
  })
})

describe('agentHandoffSurface', () => {
  it('reads question words in the instruction only', () => {
    expect(
      agentHandoffSurface('결과 보고서를 만들어줘. 자료: 납기 88% → 90%(차이 작음).', ['report'], {}),
    ).toBe('report')
  })
})
