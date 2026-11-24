import { describe, expect, it } from 'vitest'
import { SentenceFeed, speakableText } from './speech'
import { Vad } from './vad'

describe('speakableText', () => {
  it('drops markup, links, citations and tables', () => {
    expect(speakableText('## 제목')).toBe('제목')
    expect(speakableText('**신청 기간**은 10월 14일부터입니다.[1][2]')).toBe('신청 기간은 시월 십사일부터입니다.')
    expect(speakableText('[공고문](https://example.com/a) 참고')).toBe('공고문 참고')
    expect(speakableText('| 항목 | 내용 |\n| --- | --- |\n| 규모 | 2억 원 |')).toBe('')
    expect(speakableText('1. 첫째\n- 둘째')).toBe('첫째 둘째')
    expect(speakableText('`npm test`를 실행하세요 https://x.test/y')).toBe('npm test를 실행하세요')
  })
})

describe('SentenceFeed', () => {
  const run = (parts: string[]) => {
    const feed = new SentenceFeed()
    const out: string[] = []
    let text = ''
    parts.forEach((part, i) => {
      text += part
      out.push(...feed.push(text, i === parts.length - 1))
    })
    return out
  }

  it('speaks complete sentences as the answer grows and the rest at the end', () => {
    const out = run(['이번 분기 공고는 세 가지로 정리됩니다. 첫째, 신청 기', '간은 10월입니다. 둘째는 대상이고', ' 셋째는 평가입니다.'])
    expect(out.join(' ')).toBe('이번 분기 공고는 세 가지로 정리됩니다. 첫째, 신청 기간은 시월입니다. 둘째는 대상이고 셋째는 평가입니다.')
    expect(out.length).toBeGreaterThanOrEqual(2)
  })

  it('skips code blocks and tables, and does not split a list number from its item', () => {
    const answer = [
      '아래처럼 하면 됩니다.\n',
      '```python\nprint("a. b")\n```\n',
      '1. **신청 기간**은 10월 14일부터 11월 8일까지입니다.\n',
      '| 항목 | 내용 |\n| --- | --- |\n| 규모 | 2억 |\n',
      '이상입니다.',
    ]
    const out = run(answer)
    const joined = out.join(' ')
    expect(joined).not.toContain('print')
    expect(joined).not.toContain('2억')
    expect(joined).toContain('신청 기간은 시월 십사일부터 십일월 팔일까지입니다.')
    expect(joined).not.toMatch(/^1\./)
  })

  it('keeps short sentences together and cuts a very long one', () => {
    expect(run(['네. 알겠습니다. 바로 하겠습니다. 잠시만요.']).length).toBeLessThan(4)
    const long = `${'가나다라마바사 '.repeat(80)}.`
    const out = run([long])
    expect(out.every((s) => s.length <= 280)).toBe(true)
  })
})

describe('Vad', () => {
  const frames = (v: Vad, rms: number, n: number, ms = 128) =>
    Array.from({ length: n }, () => v.feed(rms, ms))

  it('starts after steady loud frames and ends after a second of quiet', () => {
    const v = new Vad()
    expect(frames(v, 0.001, 5)).toEqual([null, null, null, null, null])
    const loud = frames(v, 0.2, 3)
    expect(loud).toContain('start')
    const tail = frames(v, 0.001, 12)
    expect(tail.filter((e) => e === 'end')).toHaveLength(1)
    expect(v.speaking).toBe(false)
  })

  it('ignores a single loud frame and asks for more while the app itself is playing', () => {
    const v = new Vad()
    expect(frames(v, 0.2, 1)).toEqual([null])
    expect(frames(v, 0.001, 2)).toEqual([null, null])
    const loud = frames(v, 0.03, 4)
    expect(loud).toContain('start')
    const w = new Vad()
    expect(Array.from({ length: 4 }, () => w.feed(0.03, 128, 2.5))).toEqual([null, null, null, null])
  })

  it('ends an utterance that never goes quiet', () => {
    const v = new Vad()
    const events = frames(v, 0.2, 260)
    expect(events).toContain('start')
    expect(events).toContain('end')
  })
})
