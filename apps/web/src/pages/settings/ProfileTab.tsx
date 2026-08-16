import { Shield } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, Field, Input } from '@/components/ui'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * Name and password. Email is not editable — it is the login identity and the
 * key an admin approved, so changing it belongs behind a verification flow
 * rather than a text field.
 */
function ProfileFields() {
  const t = useT()
  const { user, updateProfile, changePassword } = useStore()
  const [name, setName] = useState(user?.name ?? '')
  const [saved, setSaved] = useState(false)
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' })
  const [pwState, setPwState] = useState<{ busy: boolean; error: string | null; done: boolean }>({
    busy: false,
    error: null,
    done: false,
  })

  useEffect(() => {
    setName(user?.name ?? '')
  }, [user?.name])

  const pwReady =
    pw.current.length > 0 && pw.next.length >= 10 && pw.next === pw.confirm

  return (
    <div className="space-y-4 border-t border-line pt-5">
        <div className="flex items-end gap-2">
          <Field label={t('이름')}>
            <Input
              value={name}
              onChange={(e) => {
                setName(e.target.value)
                setSaved(false)
              }}
              className="w-64"
            />
          </Field>
          <Button
            disabled={!name.trim() || name === user?.name}
            title={
              !name.trim()
                ? t('이름을 입력하세요')
                : name === user?.name
                  ? t('바뀐 내용이 없습니다')
                  : undefined
            }
            onClick={async () => {
              await updateProfile({ name: name.trim() })
              setSaved(true)
            }}
          >
            {t('저장')}
          </Button>
          {saved && <span className="pb-2 text-[12px] text-success">{t('저장됨')}</span>}
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t('현재 비밀번호')}>
            <Input
              type="password"
              autoComplete="current-password"
              value={pw.current}
              onChange={(e) => setPw({ ...pw, current: e.target.value })}
            />
          </Field>
          <Field label={t('새 비밀번호')} hint={t('10자 이상')}>
            <Input
              type="password"
              autoComplete="new-password"
              value={pw.next}
              onChange={(e) => setPw({ ...pw, next: e.target.value })}
            />
          </Field>
          <Field label={t('새 비밀번호 확인')}>
            <Input
              type="password"
              autoComplete="new-password"
              value={pw.confirm}
              onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
            />
          </Field>
        </div>
        <div className="flex items-center gap-2">
          <Button
            disabled={!pwReady || pwState.busy}
            title={!pwReady ? t('현재 비밀번호와 새 비밀번호를 모두 입력하세요') : undefined}
            onClick={async () => {
              setPwState({ busy: true, error: null, done: false })
              try {
                await changePassword(pw.current, pw.next)
                setPw({ current: '', next: '', confirm: '' })
                setPwState({ busy: false, error: null, done: true })
              } catch (err) {
                const detail = err instanceof ApiError ? err.detail : 'network_error'
                setPwState({
                  busy: false,
                  done: false,
                  error:
                    detail === 'wrong_current_password'
                      ? t('현재 비밀번호가 맞지 않습니다.')
                      : t('변경에 실패했습니다.'),
                })
              }
            }}
          >
            {pwState.busy ? t('변경 중…') : t('비밀번호 변경')}
          </Button>
          {pwState.done && (
            <span className="text-[12px] text-success">
              {t('변경했습니다. 다른 기기의 로그인은 모두 해제되었습니다.')}
            </span>
          )}
          {pwState.error && <span className="text-[12px] text-danger">{pwState.error}</span>}
          {pw.next.length > 0 && pw.next !== pw.confirm && (
            <span className="text-[12px] text-warn">{t('새 비밀번호가 일치하지 않습니다.')}</span>
          )}
        </div>
    </div>
  )
}

/** Identity, allowance, and the cycle it resets on. */
export function ProfileTab() {
  const t = useT()
  const { user } = useStore()
  const remaining = (user?.monthlyCredits ?? 0) - (user?.creditsUsed ?? 0)
  const pct = user?.monthlyCredits ? (user.creditsUsed / user.monthlyCredits) * 100 : 0

  return (
    <div className="space-y-6">
      <Card className="flex flex-wrap items-center gap-3.5 p-4">
        <span
          className="grid size-11 shrink-0 place-items-center rounded-full text-base font-semibold text-white"
          style={{ background: user?.avatarColor }}
        >
          {user?.name?.[0]}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{user?.name}</p>
          <p className="text-[13px] text-muted">{user?.email}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {user?.role === 'admin' && (
              <Badge tone="accent">
                <Shield size={10} />
                {t('관리자')}
              </Badge>
            )}
            <Badge tone={user?.status === 'active' ? 'success' : 'warn'}>{user?.status}</Badge>
            <Badge>{t('가입')} {user && formatDate(user.createdAt)}</Badge>
          </div>
        </div>
        <div className="text-right">
          <p className="text-[11px] text-faint">{t('남은 크레딧')}</p>
          <p className="text-lg font-semibold tabular-nums">{remaining.toLocaleString()}</p>
          <p className="text-[11px] text-faint">/ {t('{n} 월').replace('{n}', user?.monthlyCredits.toLocaleString() ?? '0')}</p>
        </div>
      </Card>

      <div>
        <div className="h-1.5 overflow-hidden rounded-full bg-elevated">
          <div
            className="h-full rounded-full bg-accent"
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
        <p className="mt-1.5 text-[11px] text-faint">
          {t('{date}에 {n} 크레딧으로 리필됩니다. 남은 크레딧은 이월되지 않습니다. 한도 변경은 관리자에게 문의하세요.')
            .replace('{date}', user ? formatDate(user.cycleResetsAt) : '')
            .replace('{n}', user?.monthlyCredits.toLocaleString() ?? '0')}
        </p>
      </div>

      <ProfileFields />
    </div>
  )
}
