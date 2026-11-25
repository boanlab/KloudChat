import { ApiError, errorMessage } from './api'

/** A save the server refused for a reason the person can act on, in words. */
export function saveFailure(err: unknown, t: (text: string) => string): string {
  if (err instanceof ApiError && err.status === 422) {
    const reasons: Record<string, string> = {
      bad_range: '시트의 범위를 벗어난 변경이 있어 저장하지 못했습니다. 표 안에서 다시 편집해 주세요.',
      bad_op: '저장할 수 없는 변경이 들어 있습니다. 새로고침해 다시 편집해 주세요.',
      not_a_xlsx_file: '이 파일은 엑셀 시트로 읽을 수 없어 저장하지 못했습니다.',
      last_sheet: '시트는 하나 이상 있어야 합니다.',
    }
    const reason = reasons[err.detail] ?? (/^(no_sheet|sheet_exists|bad_sheet_name)/.test(err.detail)
      ? '시트 이름이 맞지 않아 저장하지 못했습니다. 이름을 확인해 주세요.'
      : '')
    if (reason) return t(reason)
  }
  return errorMessage(err, t('저장하지 못했습니다. 잠시 뒤 다시 시도하세요.'))
}
