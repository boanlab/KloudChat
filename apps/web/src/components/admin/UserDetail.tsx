import { ArrowRight, KeyRound, Loader2, Shield } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Modal } from '@/components/ui'
import { CopyButton } from '@/components/ui/CopyButton'
import { RelTime } from '@/components/ui/RelTime'
import { adminApi, errorMessage, usageApi, type AuditRow, type UserOverview } from '@/lib/api'
import { actionLabel } from '@/lib/auditLabels'
import { USER_STATUS_LABEL } from '@/lib/userStatus'
import { useT } from '@/lib/useT'
import { cn, formatDate, formatDateTime } from '@/lib/utils'
import type { User } from '@/types'

const STATUS_TONE = { active: 'success', pending: 'warn', suspended: 'danger' } as const

/** This cycle's use against the monthly allowance. */
export function CreditBar({ user, className }: { user: User; className?: string }) {
  const pct = user.monthlyCredits > 0 ? (user.creditsUsed / user.monthlyCredits) * 100 : 0
  return (
    <div className={cn('w-36', className)}>
      <div className="h-1.5 overflow-hidden rounded-full bg-elevated">
        <div
          className={cn('h-full rounded-full', pct > 90 ? 'bg-danger' : 'bg-accent')}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <span className="mt-1 block text-xs tabular-nums text-faint">
        {user.creditsUsed.toLocaleString()} / {user.monthlyCredits.toLocaleString()}
      </span>
    </div>
  )
}

/** Rows of the trail shown here; the rest are a link away. */
const RECENT = 8

/** 관리자 › 사용자 detail. `row` is the list's copy when on this page, so an action's result shows at once. */
export function UserDetail({
  userId,
  row,
  onClose,
  actions,
}: {
  userId: string | null
  row: User | null
  onClose: () => void
  actions: (user: User) => ReactNode
}) {
  const t = useT()
  const [overview, setOverview] = useState<UserOverview | null>(null)
  const [trail, setTrail] = useState<AuditRow[] | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    if (!userId) return
    let live = true
    setOverview(null)
    setTrail(null)
    setFailed(null)
    adminApi
      .userOverview(userId)
      .then((found) => {
        if (!live) return
        setOverview(found)
        return usageApi.audit({ page: 1, size: RECENT, account: found.user.email }).then((page) => {
          if (live) setTrail(page.items)
        })
      })
      .catch((err) => {
        if (live) setFailed(errorMessage(err, t('사용자 정보를 불러오지 못했습니다.')))
      })
    return () => {
      live = false
    }
    // `t` changes with the language only; the account is what to load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId])

  const user = row ?? overview?.user ?? null
  const facts: [string, ReactNode][] = user
    ? [
        [t('가입'), formatDate(user.createdAt)],
        [t('마지막 활동'), user.lastActiveAt ? <RelTime key="active" iso={user.lastActiveAt} /> : t('없음')],
        [t('다음 리필'), overview?.nextReset ? formatDate(overview.nextReset) : '—'],
        [t('쓸 수 있는 모델'), user.allowedModels.length ? t('{n}개로 제한').replace('{n}', String(user.allowedModels.length)) : t('전체')],
        [t('대화'), overview ? overview.sessions.toLocaleString() : '…'],
        [t('프로젝트'), overview ? overview.projects.toLocaleString() : '…'],
        [t('파일'), overview ? overview.files.toLocaleString() : '…'],
        [t('아티팩트'), overview ? overview.artifacts.toLocaleString() : '…'],
      ]
    : []

  return (
    <Modal
      open={userId !== null}
      onClose={onClose}
      title={user ? user.name : t('사용자')}
      description={user?.email}
      width="max-w-2xl"
      footer={user ? <div className="flex flex-wrap items-center justify-end gap-1.5">{actions(user)}</div> : undefined}
    >
      <div data-testid="user-detail" className="space-y-5">
        {failed && (
          <p role="alert" className="rounded-card border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger">
            {failed}
          </p>
        )}
        {!user && !failed && <Loader2 size={18} className="mx-auto animate-spin text-faint" />}
        {user && (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <span
                className="grid size-10 shrink-0 place-items-center rounded-full text-base font-semibold text-white"
                style={{ background: user.avatarColor }}
              >
                {user.name[0]}
              </span>
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-1.5 text-md font-semibold">
                  {user.name}
                  <Badge tone={STATUS_TONE[user.status]}>{t(USER_STATUS_LABEL[user.status])}</Badge>
                  {user.role === 'admin' && (
                    <Badge tone="accent">
                      <Shield size={10} />
                      {t('관리자')}
                    </Badge>
                  )}
                  {user.emailVerifiedAt === null && <Badge>{t('메일 미확인')}</Badge>}
                </p>
                <p className="flex min-w-0 items-center gap-0.5 text-sm text-muted">
                  <span className="truncate">{user.email}</span>
                  <CopyButton text={user.email} label={t('이메일')} className="size-5" />
                </p>
                <p className="mt-0.5 flex items-center gap-1 text-xs text-faint">
                  <KeyRound size={10} />
                  {user.litellmKeyPreview ? (
                    <span className="font-mono">{user.litellmKeyPreview}</span>
                  ) : (
                    <span className="text-warn">{t('전용 키 없음')}</span>
                  )}
                </p>
              </div>
            </div>

            <section className="space-y-1.5">
              <h3 className="text-base font-semibold">{t('이번 달 크레딧')}</h3>
              <CreditBar user={user} className="w-full max-w-sm" />
            </section>

            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4" data-testid="user-facts">
              {facts.map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-xs text-faint">{label}</dt>
                  <dd className="truncate text-base tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>

            <section className="space-y-1.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-base font-semibold">{t('최근 기록')}</h3>
                <Link
                  to={`/admin/governance?tab=log&account=${encodeURIComponent(user.email)}`}
                  className="flex items-center gap-1 text-sm text-accent hover:underline"
                >
                  {t('감사 로그에서 모두 보기')}
                  <ArrowRight size={13} />
                </Link>
              </div>
              {trail === null ? (
                !failed && <Loader2 size={15} className="animate-spin text-faint" />
              ) : trail.length === 0 ? (
                <p className="text-sm text-faint">{t('이 계정에 대한 기록이 없습니다.')}</p>
              ) : (
                <ul className="divide-y divide-line rounded-card border border-line" data-testid="user-trail">
                  {trail.map((entry) => (
                    <li key={entry.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2 text-sm">
                      <span className="font-medium">{t(actionLabel(entry))}</span>
                      <span className="min-w-0 flex-1 truncate text-muted">
                        {entry.actor === user.email ? t('본인') : entry.actor}
                      </span>
                      <span className="text-xs text-faint tabular-nums">{formatDateTime(entry.at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </Modal>
  )
}
