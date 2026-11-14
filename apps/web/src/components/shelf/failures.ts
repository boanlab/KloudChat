/** Why a guide could not be made, in words. */
export function shelfFailure(code: string | undefined, t: (s: string) => string): string {
  switch (code) {
    case 'no_strict_local_model':
      return t('자료를 안에서만 다루는 모델(strict-local)이 지금 없어 만들 수 없습니다. 관리자에게 문의하세요.')
    case 'internal_data_requires_strict_local':
      return t('내부 자료는 strict-local 모델로만 다룹니다. 다른 모델은 고를 수 없습니다.')
    case 'insufficient_credits':
      return t('크레딧이 모자랍니다.')
    case 'no_sources':
    case 'nothing_to_read':
      return t('읽을 수 있는 자료가 없습니다.')
    case 'governance_unavailable':
      return t('관리 정책을 확인하지 못해 멈췄습니다. 잠시 후 다시 시도하세요.')
    default:
      return t('만들지 못했습니다. 다시 시도하세요.')
  }
}
