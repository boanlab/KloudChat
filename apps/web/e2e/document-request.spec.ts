/** Which chat sentences move to the report or slides screen. No browser. */

import { expect, test } from '@playwright/test'
import { handoffSurface } from '../src/lib/documentRequest'

test('보도자료·공지문 초안은 챗에서 짧은 글로 쓴다', () => {
  expect(handoffSurface('보도자료 초안 써줘. 회사: 노바랩스, 대표 이름은 이서연. 문의처는 pr@novalabs.example.')).toBeNull()
  expect(handoffSurface('신입생 오리엔테이션 공지문 작성해 줘')).toBeNull()
  expect(handoffSurface('press release draft for our launch, please write it')).toBeNull()
})

test('보고서·발표 자료를 시키면 그 화면으로 넘긴다', () => {
  expect(handoffSurface('국내 AI 회의록 서비스 시장 조사 보고서를 만들어줘')).toBe('report')
  expect(handoffSurface('지난 보도자료들을 모아 홍보 실적 보고서를 작성해 줘')).toBe('report')
  expect(handoffSurface('분기 실적 발표 슬라이드 만들어 줘')).toBe('slides')
})
