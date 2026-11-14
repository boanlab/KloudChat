import { describe, expect, it } from 'vitest'
import { clock, frameAt, linkTimestamps, secondsOf, VIDEO_LINK } from '@/lib/media'
import type { MediaInfo } from '@/lib/api'

const lecture = { id: 'v1', name: '강의.mp4' }
const demo = { id: 'v2', name: '시연.webm' }

describe('time marks in an answer', () => {
  it('links a mark to the only video of the conversation', () => {
    expect(linkTimestamps('보상 회로를 설명합니다 [00:12:30].', [lecture]))
      .toBe('보상 회로를 설명합니다 [00:12:30](/files?open=v1&t=750).')
  })

  it('picks the video named just before the mark', () => {
    const text = '시연.webm에서 코드를 고칩니다 [3:05]. 강의.mp4 [1:02:03]에서는 이론을 봅니다.'
    expect(linkTimestamps(text, [lecture, demo])).toBe(
      '시연.webm에서 코드를 고칩니다 [3:05](/files?open=v2&t=185). 강의.mp4 [1:02:03](/files?open=v1&t=3723)에서는 이론을 봅니다.',
    )
  })

  it('follows the video the answer already cites', () => {
    const text = '늘었습니다 [[1]](/files?open=v2&t=10). 그 뒤 [00:00:40]에 다시 나옵니다.'
    expect(linkTimestamps(text, [lecture, demo])).toContain('[00:00:40](/files?open=v2&t=40)')
  })

  it('leaves a mark alone when the video is unclear, in code, or already a link', () => {
    expect(linkTimestamps('[12:30]', [lecture, demo])).toBe('[12:30]')
    expect(linkTimestamps('`[12:30]`', [lecture])).toBe('`[12:30]`')
    expect(linkTimestamps('[12:30](/x)', [lecture])).toBe('[12:30](/x)')
    expect(linkTimestamps('[12:75]', [lecture])).toBe('[12:75]')
  })

  it('reads the app link back', () => {
    expect(VIDEO_LINK.exec('/files?open=v1&t=750')?.slice(1)).toEqual(['v1', '750'])
    expect(VIDEO_LINK.exec('https://evil.test/files?open=v1&t=1')).toBeNull()
  })
})

describe('clock', () => {
  it('writes and reads times', () => {
    expect(clock(750)).toBe('12:30')
    expect(clock(3723)).toBe('1:02:03')
    expect(secondsOf('00:12:30')).toBe(750)
    expect(secondsOf('a:b')).toBeNull()
  })

  it('finds the picture on screen at a moment', () => {
    const info = { frames: [{ n: 0, t: 0 }, { n: 1, t: 30 }, { n: 2, t: 90 }] } as MediaInfo
    expect(frameAt(info, 45)).toBe(1)
    expect(frameAt(info, 90)).toBe(2)
    expect(frameAt(info, 0)).toBe(0)
  })
})
