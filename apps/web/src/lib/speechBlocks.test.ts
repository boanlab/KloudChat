import { describe, expect, it } from 'vitest'
import { BlockFeed, type SpokenBlock } from './speechBlocks'

/** The answer arrives in pieces; what each push returns is collected. */
function run(chunks: string[], final = true): SpokenBlock[] {
  const feed = new BlockFeed()
  const out: SpokenBlock[] = []
  let text = ''
  for (const chunk of chunks) {
    text += chunk
    out.push(...feed.push(text, false))
  }
  if (final) out.push(...feed.push(text, true))
  return out
}

describe('BlockFeed', () => {
  it('reads a paragraph when its blank line arrives, and the last one at the end', () => {
    const out = run(['첫 문단입니다. 두 문장입니다.\n', '\n두 번째 문단입니다.'], false)
    expect(out.map((b) => b.kind)).toEqual(['para'])
    expect(out[0].source).toBe('첫 문단입니다. 두 문장입니다.')
    const all = run(['첫 문단입니다. 두 문장입니다.\n\n두 번째 문단입니다.'])
    expect(all.map((b) => b.source)).toEqual(['첫 문단입니다. 두 문장입니다.', '두 번째 문단입니다.'])
  })

  it('reads a bulleted list as one block, with the items as written', () => {
    const out = run(['세 가지입니다.\n\n- 비용 3.5% 증가\n- 기간 10km\n- 담당 (김 팀장)\n\n끝.'])
    expect(out.map((b) => b.kind)).toEqual(['para', 'list', 'para'])
    expect(out[1].source).toBe('- 비용 3.5% 증가\n- 기간 10km\n- 담당 (김 팀장)')
  })

  it('reads a numbered list as one block and says a long one in part when it cannot be rewritten', () => {
    const items = ['하나', '둘', '셋', '넷', '다섯', '여섯']
    const out = run([items.map((x, i) => `${i + 1}. ${x}`).join('\n')])
    expect(out).toHaveLength(1)
    expect(out[0].kind).toBe('list')
    expect(out[0].text).toBe('하나. 둘. 셋. 넷. 그 밖에 몇 가지가 더 있습니다.')
    expect(out[0].source.split('\n')).toHaveLength(6)
  })

  it('reads a table as one block; the fallback only points at the screen', () => {
    const out = run(['| 항목 | 값 |\n|---|---|\n| 비용 | 3.5% |\n| 기간 | 2주 |\n\n표 아래 문장입니다.'])
    expect(out.map((b) => b.kind)).toEqual(['table', 'para'])
    expect(out[0].source).toBe('항목 | 값\n비용 | 3.5%\n기간 | 2주')
    expect(out[0].text).toBe('표는 화면에 적어 두었습니다.')
  })

  it('does not read code, only says where it is', () => {
    const out = run(['아래 코드를 보세요.\n\n```python\nprint(1)\n```\n\n이상입니다.'])
    expect(out.map((b) => b.kind)).toEqual(['para', 'code', 'para'])
    expect(out[1]).toEqual({ kind: 'code', source: '', text: '코드는 화면에 적어 두었습니다.' })
    expect(JSON.stringify(out)).not.toContain('print')
  })

  it('says where the code is when the answer stops inside it', () => {
    const out = run(['```js\nlet a = 1'])
    expect(out.map((b) => b.kind)).toEqual(['code'])
  })

  it('puts a heading in front of the block it opens', () => {
    const out = run(['## 요약\n\n핵심은 비용입니다.'])
    expect(out).toHaveLength(1)
    expect(out[0].source).toBe('요약. 핵심은 비용입니다.')
  })

  it('removes citation numbers and links from the source', () => {
    const out = run(['자세한 내용은 [공고문](https://example.org/a)에 있습니다.[1][2]'])
    expect(out[0].source).toBe('자세한 내용은 공고문에 있습니다.')
  })

  it('starts the first block at its first complete sentence instead of its end', () => {
    const feed = new BlockFeed()
    const first = feed.push('네, 신청 기간은 시월 십사일부터 십일월 팔일까지입니다. 대상은', false)
    expect(first.map((b) => b.source)).toEqual(['네, 신청 기간은 시월 십사일부터 십일월 팔일까지입니다.'])
    const rest = feed.push('네, 신청 기간은 시월 십사일부터 십일월 팔일까지입니다. 대상은 중소기업입니다.\n\n', false)
    expect(rest.map((b) => b.source)).toEqual(['대상은 중소기업입니다.'])
  })

  it('does not read a sentence twice when the paragraph closes', () => {
    const out = run([
      '네, 신청 기간은 시월 십사일부터 십일월 팔일까지입니다. ',
      '대상은 중소기업입니다. 접수는 온라인으로 합니다.\n\n다음 문단입니다.',
    ])
    expect(out.map((b) => b.source).join(' ')).toBe(
      '네, 신청 기간은 시월 십사일부터 십일월 팔일까지입니다. 대상은 중소기업입니다. 접수는 온라인으로 합니다. 다음 문단입니다.',
    )
  })

  it('cuts a long paragraph into pieces at sentence ends', () => {
    const sentence = '이 문장은 길이를 채우기 위해 일부러 길게 적은 문장입니다.'
    const out = run([Array.from({ length: 12 }, () => sentence).join(' ')])
    expect(out.length).toBeGreaterThan(1)
    expect(out.every((b) => b.source.length <= 300)).toBe(true)
    expect(out.every((b) => b.source.endsWith('.'))).toBe(true)
  })

  it('gives rule-cleaned text as the fallback, in Hangul', () => {
    const out = run(['API는 3.5% 늘었고 CPU는 10km입니다.'])
    expect(out[0].text).toBe('에이피아이는 삼 점 오 퍼센트 늘었고 씨피유는 십 킬로미터입니다.')
  })

  it('reads nothing for markup alone, and keeps the order across kinds', () => {
    expect(run(['---\n\n***\n'])).toEqual([])
    const out = run(['첫 문단입니다.\n\n- 가\n- 나\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n끝 문단입니다.'])
    expect(out.map((b) => b.kind)).toEqual(['para', 'list', 'table', 'para'])
  })

  it('does not repeat a block when pushed again with the same text', () => {
    const feed = new BlockFeed()
    const text = '첫 문단입니다.\n\n둘째 문단입니다.\n\n'
    const a = feed.push(text, false)
    const b = feed.push(text, false)
    expect(a).toHaveLength(2)
    expect(b).toEqual([])
  })
})
