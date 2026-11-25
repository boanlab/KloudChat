import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api'
import { saveFailure } from './useOfficeDocument'

const t = (text: string) => text

describe('a save the server refused', () => {
  it('says why when the reason is one the person can act on', () => {
    expect(saveFailure(new ApiError(422, 'bad_range'), t)).toContain('범위를 벗어난')
    expect(saveFailure(new ApiError(422, 'no_sheet:Sheet9'), t)).toContain('시트 이름')
    expect(saveFailure(new ApiError(422, 'not_a_xlsx_file'), t)).toContain('엑셀 시트로 읽을 수 없어')
  })

  it('keeps a sentence the server wrote and falls back for anything else', () => {
    expect(saveFailure(new ApiError(422, '시트 파일이 너무 큽니다.'), t)).toBe('시트 파일이 너무 큽니다.')
    expect(saveFailure(new ApiError(500, 'boom'), t)).toBe('저장하지 못했습니다. 잠시 뒤 다시 시도하세요.')
  })
})
