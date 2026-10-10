import type { UserStatus } from '@/types'

/** An account's status as the screens name it. */
export const USER_STATUS_LABEL: Record<UserStatus, string> = {
  active: '활성',
  pending: '승인 대기',
  suspended: '정지',
}
