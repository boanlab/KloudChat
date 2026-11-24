import { describe, expect, it } from 'vitest'
import { forSpeech, koreanLike, native, readAsides, readLatin, readNumbers, sino } from './speechKo'
import { speakableText } from './speech'

describe('sino and native numbers', () => {
  it('reads Sino-Korean numbers', () => {
    expect(sino(0)).toBe('영')
    expect(sino(10)).toBe('십')
    expect(sino(2026)).toBe('이천이십육')
    expect(sino(10000)).toBe('만')
    expect(sino(12345)).toBe('만 이천삼백사십오')
    expect(sino(100000000)).toBe('일억')
  })
  it('reads native numbers before a counter', () => {
    expect([1, 2, 3, 4, 14, 20, 21, 99].map(native)).toEqual(
      ['한', '두', '세', '네', '열네', '스무', '스물한', '아흔아홉'],
    )
    expect(native(100)).toBe('백')
  })
})

describe('readNumbers', () => {
  it('reads decimals, commas and percent', () => {
    expect(readNumbers('3.5')).toBe('삼 점 오')
    expect(readNumbers('1,000원')).toBe('천원')
    expect(readNumbers('10,000명')).toBe('만 명')
  })
  it('reads units', () => {
    expect(readNumbers('10km')).toBe('십 킬로미터')
    expect(readNumbers('2.5 GB')).toBe('이 점 오 기가바이트')
    expect(readNumbers('5kg')).toBe('오 킬로그램')
    expect(readNumbers('36.5°C')).toBe('삼십육 점 오 도')
  })
  it('reads dates and times', () => {
    expect(readNumbers('2026년 11월 24일')).toBe('이천이십육년 십일월 이십사일')
    expect(readNumbers('6월과 10월')).toBe('유월과 시월')
    expect(readNumbers('14:30')).toBe('열네 시 삼십분')
    expect(readNumbers('9:00')).toBe('아홉 시')
  })
  it('reads counters in native numbers', () => {
    expect(readNumbers('3개')).toBe('세 개')
    expect(readNumbers('2시간')).toBe('두 시간')
    expect(readNumbers('3개월')).toBe('삼개월')
  })
  it('reads ranges and phone numbers', () => {
    expect(readNumbers('10~20')).toBe('십에서 이십')
    expect(readNumbers('010-1234-5678')).toBe('공일공 일이삼사 오육칠팔')
  })
})

describe('readLatin', () => {
  it('reads acronyms in Hangul', () => {
    expect(readLatin('API와 CPU')).toBe('에이피아이와 씨피유')
    expect(readLatin('APIs')).toBe('에이피아이')
    expect(readLatin('R&D 비용')).toBe('알앤디 비용')
  })
  it('reads common words and spells unknown capitals', () => {
    expect(readLatin('cloud computing')).toBe('클라우드 컴퓨팅')
    expect(readLatin('Cloud')).toBe('클라우드')
    expect(readLatin('NLP')).toBe('엔엘피')
  })
  it('leaves other words alone', () => {
    expect(readLatin('Rhinoceros')).toBe('Rhinoceros')
  })
})

describe('readAsides', () => {
  it('reads a short Hangul aside as part of the sentence', () => {
    expect(readAsides('서버(저장 장치)를 쓴다')).toContain(', 저장 장치,')
  })
  it('drops Latin-only and long asides', () => {
    expect(readAsides('클라우드(cloud computing)는').replace(/\s+/g, ' ')).toBe('클라우드 는')
    expect(readAsides('값(이 값은 아주 길어서 괄호 안에 들어가기에는 너무 많은 설명이 담겨 있다)입니다'))
      .not.toContain('길어서')
  })
})

describe('forSpeech', () => {
  it('turns a mixed Korean sentence into Hangul', () => {
    expect(forSpeech('API 응답은 3.5% 빠르고 CPU는 10km 같아요.'))
      .toBe('에이피아이 응답은 삼 점 오 퍼센트 빠르고 씨피유는 십 킬로미터 같아요.')
  })
  it('turns arrows, slashes, dots and emoji into pauses', () => {
    expect(forSpeech('입력 → 처리 → 출력 🙂')).toBe('입력, 처리, 출력')
    expect(forSpeech('읽기/쓰기')).toBe('읽기 또는 쓰기')
    expect(forSpeech('속도·비용')).toBe('속도, 비용')
  })
  it('leaves an English answer as it is', () => {
    expect(forSpeech('The API costs 3.5% more.')).toBe('The API costs 3.5% more.')
  })
  it('detects the language of a text', () => {
    expect(koreanLike('안녕하세요 hello')).toBe(true)
    expect(koreanLike('Hello there')).toBe(false)
    expect(koreanLike('12345')).toBe(false)
  })
})

describe('speakableText with the spoken forms', () => {
  it('reads a heading, a bullet and a table row as speech', () => {
    expect(speakableText('## 요약')).toBe('요약')
    expect(speakableText('- 비용은 1,000원(부가세 별도)입니다.'))
      .toBe('비용은 천원, 부가세 별도, 입니다.')
    expect(speakableText('| a | b |')).toBe('')
  })
  it('keeps the sentence end', () => {
    expect(speakableText('3개가 있어요.')).toBe('세 개가 있어요.')
  })
})
