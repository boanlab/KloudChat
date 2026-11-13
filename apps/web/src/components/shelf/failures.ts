import type { StudioEstimate } from '@/lib/shelf'

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

/** Speech in words, by the plan the server gives. */
export function speechNote(plan: StudioEstimate['speech'], t: (s: string) => string): string {
  if (!plan) return ''
  if (plan.mode === 'local') return t('음성은 안에서 돌아가는 음성 모델로 만듭니다.')
  if (plan.mode === 'outside') {
    return t('음성은 바깥 음성 모델({model})로 만듭니다. 대본(개인정보는 가림)이 바깥으로 나가므로 만들기 전에 한 번 더 묻습니다. 약 {n} 크레딧.')
      .replace('{model}', plan.model ?? '')
      .replace('{n}', String(plan.credits ?? 0))
  }
  if (plan.reason === 'strict_local_no_tts') {
    return t('이 프로젝트 자료는 안에서만 다루도록 정해져 있고, 안에서 돌아가는 음성 모델이 없어 대본만 만듭니다.')
  }
  return t('쓸 수 있는 음성 모델이 없어 대본만 만듭니다.')
}
