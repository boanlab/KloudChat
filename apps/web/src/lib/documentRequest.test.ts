import { describe, expect, it } from 'vitest'
import {
  agentHandoffSurface, handoffSurface, NAMES_DOCX, NAMES_HWP, NAMES_PPTX, officeReportRequest,
  reportFileKind,
} from '@/lib/documentRequest'

describe('handoffSurface', () => {
  it('keeps a press release or notice draft in chat as short writing', () => {
    expect(handoffSurface('보도자료 초안 써줘. 회사: 노바랩스, 대표 이름은 이서연. 문의처는 pr@novalabs.example.')).toBeNull()
    expect(handoffSurface('신입생 오리엔테이션 공지문 작성해 줘')).toBeNull()
    expect(handoffSurface('press release draft for our launch, please write it')).toBeNull()
  })

  it('moves a report or a deck to its screen', () => {
    expect(handoffSurface('국내 AI 회의록 서비스 시장 조사 보고서를 만들어줘')).toBe('report')
    expect(handoffSurface('지난 보도자료들을 모아 홍보 실적 보고서를 작성해 줘')).toBe('report')
    expect(handoffSurface('분기 실적 발표 슬라이드 만들어 줘')).toBe('slides')
  })

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

describe('NAMES_HWP', () => {
  it('names an HWP document in its usual spellings and nothing else', () => {
    for (const text of ['공문을 hwp로 만들어 줘', '한글 문서로 써 줘', 'HWPX 파일로', '아래아한글 파일로']) {
      expect(NAMES_HWP.test(text)).toBe(true)
    }
    for (const text of ['한글로 번역해 줘', 'whwpx', '한글날 행사 공문을 작성해 줘']) {
      expect(NAMES_HWP.test(text)).toBe(false)
    }
  })
})

describe('NAMES_DOCX', () => {
  it('names a Word document as the server does, and not words that end in 워드', () => {
    for (const text of [
      '공문을 Word로 작성해 주세요', '워드 문서로 써 줘', 'docx 파일로', 'MS Word 파일',
      'a Word document please', '워드로 만들어 줘',
    ]) {
      expect(NAMES_DOCX.test(text)).toBe(true)
    }
    for (const text of ['키워드로 검색해 줘', '패스워드 파일', 'in a word, no', '한글 문서로', 'docxy']) {
      expect(NAMES_DOCX.test(text)).toBe(false)
    }
  })

  it('keeps a Word request off the report screen', () => {
    // the composer checks NAMES_DOCX before handing a document order over
    expect(handoffSurface('직원 연수 참석을 요청하는 공문을 Word로 작성해 주세요')).not.toBeNull()
    expect(NAMES_DOCX.test('직원 연수 참석을 요청하는 공문을 Word로 작성해 주세요')).toBe(true)
  })
})

describe('NAMES_PPTX', () => {
  it('names a PowerPoint file as the server does, and not a plain deck request', () => {
    for (const text of [
      '사업 계획을 PowerPoint로 5장 만들어 줘', '파워포인트 파일로', 'pptx 파일로 줘',
      'PPT 파일로 만들어 주세요', 'a Power Point deck', 'MS PowerPoint',
    ]) {
      expect(NAMES_PPTX.test(text)).toBe(true)
    }
    for (const text of [
      '발표 자료 만들어 줘', 'PPT 만들어 줘', '사업 계획 발표 슬라이드 5장', 'pptxy', 'appt 파일',
      'Word로 작성해 줘',
    ]) {
      expect(NAMES_PPTX.test(text)).toBe(false)
    }
  })

  it('keeps a PowerPoint request off the slides screen, and a plain deck request on it', () => {
    // the composer checks NAMES_PPTX before handing a deck order over
    const asked = '사업 계획 발표 자료 5장을 PowerPoint로 만들어 주세요'
    expect(handoffSurface(asked)).toBe('slides')
    expect(NAMES_PPTX.test(asked)).toBe(true)
    expect(handoffSurface('사업 계획 발표 자료 5장 만들어 줘')).toBe('slides')
    expect(NAMES_PPTX.test('사업 계획 발표 자료 5장 만들어 줘')).toBe(false)
  })
})

describe('officeReportRequest', () => {
  it('sends a long report asked for as an HWP or Word file to the report screen', () => {
    expect(officeReportRequest('한글로 2027 사업계획 보고서 써줘')).toBe('hwp')
    expect(officeReportRequest('한글 문서로 3분기 실적 보고서 작성해 줘')).toBe('hwp')
    expect(officeReportRequest('Word 파일로 신사업 제안서 써 줘')).toBe('docx')
    expect(officeReportRequest('write the quarterly report as a Word document')).toBe('docx')
  })

  it('keeps a short document in a file, an edit or a question beside the chat', () => {
    expect(officeReportRequest('직원 연수 참석을 요청하는 공문을 Word로 작성해 주세요')).toBeNull()
    expect(officeReportRequest('한글 문서로 한 장짜리 출장 보고서 써 줘')).toBeNull()
    expect(officeReportRequest('한글 파일로 회의 메모 정리해 줘')).toBeNull()
    expect(officeReportRequest('한글 문서로 보고서 쓰는 방법 알려줘')).toBeNull()
    expect(officeReportRequest('이 보고서를 Word로 고쳐 줘')).toBeNull()
    // no file named: the HTML report as before
    expect(officeReportRequest('2027 사업계획 보고서 써줘')).toBeNull()
  })

  it('reads 「한글로」 as the language when the sentence talks about language', () => {
    expect(reportFileKind('영어 말고 한글로 보고서 써 줘')).toBeNull()
    expect(reportFileKind('한글로 2027 사업계획 보고서 써줘')).toBe('hwp')
    // the first file named, as the server decides (`report_office.asked_file`)
    expect(reportFileKind('워드 말고 한글 파일로 보고서 써 줘')).toBe('hwp')
    expect(reportFileKind('Word 파일로, 안 되면 한글 파일로 보고서 써 줘')).toBe('docx')
    expect(reportFileKind('보고서 써 줘\n\n덧붙인 조건:\n- 한글 파일로 제출')).toBeNull()
  })
})
